"""Pixel-level screenshot comparison for visual regression testing."""
from __future__ import annotations

import dataclasses
import io
import itertools
import json
import math
import struct
import sys
from collections import deque
from collections.abc import Sequence
from pathlib import Path
from typing import Any

if sys.version_info < (3, 11):
    from typing_extensions import Self
else:
    from typing import Self


def _require_pil_numpy() -> tuple[Any, Any]:
    """Lazily import Pillow and numpy, raising if missing."""
    try:
        from PIL import Image
        import numpy as np
    except ImportError as exc:
        raise RuntimeError("visual diff requires Pillow and numpy") from exc
    return Image, np


@dataclasses.dataclass(frozen=True)
class Region:
    """A rectangular region with changed pixels."""
    x: int
    y: int
    width: int
    height: int
    changed_pixels: int


@dataclasses.dataclass(frozen=True)
class DiffResult:
    """Result of comparing two images."""
    identical: bool
    size_mismatch: bool
    width: int
    height: int
    changed_pixels: int
    changed_ratio: float
    regions: tuple[Region, ...]
    max_channel_delta: int


def _decode_png_header(png_bytes: bytes) -> tuple[int, int, int, int, int, int, int]:
    """Parse PNG header to extract width, height, bit depth, color type, compression, filter, interlace.
    
    Returns (width, height, bit_depth, color_type, compression, filter, interlace).
    Only reads the IHDR chunk.
    """
    if len(png_bytes) < 33:
        raise ValueError("PNG too short for IHDR chunk")
    if png_bytes[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError("Invalid PNG signature")
    
    # Parse chunk length and type
    length = struct.unpack('>I', png_bytes[8:12])[0]
    if length < 13:
        raise ValueError("IHDR chunk too short")
    
    chunk_type = png_bytes[12:16]
    if chunk_type != b'IHDR':
        raise ValueError(f"Expected IHDR chunk, got {chunk_type}")
    
    width = struct.unpack('>I', png_bytes[16:20])[0]
    height = struct.unpack('>I', png_bytes[20:24])[0]
    bit_depth = png_bytes[24]
    color_type = png_bytes[25]
    compression = png_bytes[26]
    filter_method = png_bytes[27]
    interlace = png_bytes[28]
    
    return width, height, bit_depth, color_type, compression, filter_method, interlace


def _load_png_rgba(png_bytes: bytes) -> tuple[int, int, memoryview]:
    """Load PNG and return (width, height, rgba_data as memoryview).
    
    Handles various color types and converts to RGBA.
    """
    Image, np = _require_pil_numpy()
    
    with Image.open(io.BytesIO(png_bytes)) as img:
        # Convert to RGBA (8-bit per channel)
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        
        width, height = img.size
        # Get raw bytes
        data = img.tobytes()
        return width, height, memoryview(data)


def _parse_ignore_regions(ignore: Sequence[tuple[int, int, int, int]], width: int, height: int) -> set[tuple[int, int]]:
    """Parse ignore regions into a set of grid cell coordinates to exclude."""
    ignored = set()
    for x, y, w, h in ignore:
        x1 = max(0, x)
        y1 = max(0, y)
        x2 = min(width, x + w)
        y2 = min(height, y + h)
        if x1 < x2 and y1 < y2:
            for gy in range(y1, y2):
                for gx in range(x1, x2):
                    ignored.add((gx, gy))
    return ignored


def _find_changed_pixels(
    baseline_rgba: memoryview,
    candidate_rgba: memoryview,
    width: int,
    height: int,
    tolerance: int,
    ignore: set[tuple[int, int]],
) -> set[tuple[int, int]]:
    """Find all pixels that differ beyond tolerance, excluding ignored regions.
    
    Returns set of (x, y) coordinates.
    """
    changed = set()
    
    # Each pixel is 4 bytes (RGBA)
    for y in range(height):
        for x in range(width):
            if (x, y) in ignore:
                continue
            
            offset = (y * width + x) * 4
            br = baseline_rgba[offset]
            bg = baseline_rgba[offset + 1]
            bb = baseline_rgba[offset + 2]
            ba = baseline_rgba[offset + 3]
            
            cr = candidate_rgba[offset]
            cg = candidate_rgba[offset + 1]
            cb = candidate_rgba[offset + 2]
            ca = candidate_rgba[offset + 3]
            
            # Compare RGB channels only (ignore alpha)
            if (abs(br - cr) > tolerance or 
                abs(bg - cg) > tolerance or 
                abs(bb - cb) > tolerance):
                changed.add((x, y))
    
    return changed


def _compute_max_delta(
    baseline_rgba: memoryview,
    candidate_rgba: memoryview,
    changed: set[tuple[int, int]],
    width: int,
    height: int,
) -> int:
    """Compute maximum channel delta among changed pixels."""
    if not changed:
        return 0
    
    max_delta = 0
    for x, y in changed:
        offset = (y * width + x) * 4
        for i in range(3):  # RGB only
            delta = abs(baseline_rgba[offset + i] - candidate_rgba[offset + i])
            if delta > max_delta:
                max_delta = delta
    return max_delta


def _group_regions(
    changed: set[tuple[int, int]],
    width: int,
    height: int,
    cell: int,
    max_regions: int,
) -> tuple[Region, ...]:
    """Group changed pixels into regions using a grid-based approach.
    
    Marks changed pixels on a grid, merges 4-connected cells, and returns
    the largest max_regions by changed pixel count, deterministically ordered.
    """
    if not changed:
        return ()
    
    # Create grid cells
    # Each cell covers cell x cell pixels
    grid_width = (width + cell - 1) // cell
    grid_height = (height + cell - 1) // cell
    
    # Map grid cells to changed pixel counts and pixel coordinates
    cell_pixels: dict[tuple[int, int], int] = {}
    cell_coords: dict[tuple[int, int], list[tuple[int, int]]] = {}
    
    for x, y in changed:
        cx = x // cell
        cy = y // cell
        cell_pixels[(cx, cy)] = cell_pixels.get((cx, cy), 0) + 1
        if (cx, cy) not in cell_coords:
            cell_coords[(cx, cy)] = []
        cell_coords[(cx, cy)].append((x, y))
    
    if not cell_pixels:
        return ()
    
    # Find connected components of cells (4-connected)
    # Two cells are connected if they share an edge
    def get_neighbors(cx: int, cy: int) -> list[tuple[int, int]]:
        return [(cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1)]
    
    def bfs(start: tuple[int, int], remaining: set[tuple[int, int]]) -> set[tuple[int, int]]:
        """BFS to find all 4-connected cells."""
        component = set()
        queue = deque([start])
        while queue:
            cell = queue.popleft()
            if cell not in remaining:
                continue
            remaining.discard(cell)
            component.add(cell)
            for nx, ny in get_neighbors(cell[0], cell[1]):
                if (nx, ny) in remaining:
                    queue.append((nx, ny))
        return component
    
    # Group cells into components
    remaining_cells = set(cell_pixels.keys())
    components: list[set[tuple[int, int]]] = []
    
    while remaining_cells:
        start = next(iter(remaining_cells))
        component = bfs(start, remaining_cells)
        if component:
            components.append(component)
    
    # Compute aggregate stats for each component
    region_stats = []
    for component in components:
        total_pixels = sum(cell_pixels[cell] for cell in component)
        
        # Find bounding box
        min_x = min(c[0] for c in component) * cell
        max_x = max(c[0] for c in component) * cell + cell - 1
        min_y = min(c[1] for c in component) * cell
        max_y = max(c[1] for c in component) * cell + cell - 1
        
        # Clamp to image bounds
        max_x = min(max_x, width - 1)
        max_y = min(max_y, height - 1)
        
        region = Region(
            x=min_x,
            y=min_y,
            width=max_x - min_x + 1,
            height=max_y - min_y + 1,
            changed_pixels=total_pixels,
        )
        region_stats.append(region)
    
    # Sort by changed_pixels descending, then y, then x for determinism
    region_stats.sort(key=lambda r: (-r.changed_pixels, r.y, r.x))
    
    # Take top max_regions
    return tuple(region_stats[:max_regions])


def diff_images(
    baseline_png: bytes,
    candidate_png: bytes,
    *,
    tolerance: int = 0,
    cell: int = 16,
    max_regions: int = 20,
    ignore: Sequence[tuple[int, int, int, int]] = (),
) -> DiffResult:
    """Compare two PNG images and return a diff result.
    
    Args:
        baseline_png: Raw PNG bytes of the baseline image.
        candidate_png: Raw PNG bytes of the candidate image.
        tolerance: Maximum channel difference to still consider pixels identical.
        cell: Grid cell size for region grouping.
        max_regions: Maximum number of regions to return.
        ignore: Sequence of (x, y, w, h) rectangles to exclude from comparison.
    
    Returns:
        DiffResult with comparison statistics and regions.
    """
    # Decode headers to get dimensions
    b_width, b_height, _, b_color_type, _, _, _ = _decode_png_header(baseline_png)
    c_width, c_height, _, c_color_type, _, _, _ = _decode_png_header(candidate_png)
    
    # Check size mismatch
    if b_width != c_width or b_height != c_height:
        return DiffResult(
            identical=False,
            size_mismatch=True,
            width=max(b_width, c_width),
            height=max(b_height, c_height),
            changed_pixels=b_width * b_height + c_width * c_height,
            changed_ratio=1.0,
            regions=(),
            max_channel_delta=0,
        )
    
    width, height = b_width, b_height
    
    # Load both images as RGBA
    b_width, b_height, b_rgba = _load_png_rgba(baseline_png)
    c_width, c_height, c_rgba = _load_png_rgba(candidate_png)
    
    # Parse ignore regions
    ignore_set = _parse_ignore_regions(ignore, width, height)
    
    # Find changed pixels
    changed = _find_changed_pixels(b_rgba, c_rgba, width, height, tolerance, ignore_set)
    
    changed_count = len(changed)
    total_pixels = width * height
    changed_ratio = changed_count / total_pixels if total_pixels > 0 else 0.0
    
    # Compute max delta
    max_delta = _compute_max_delta(b_rgba, c_rgba, changed, width, height)
    
    # Group into regions
    regions = _group_regions(changed, width, height, cell, max_regions)
    
    return DiffResult(
        identical=changed_count == 0,
        size_mismatch=False,
        width=width,
        height=height,
        changed_pixels=changed_count,
        changed_ratio=changed_ratio,
        regions=regions,
        max_channel_delta=max_delta,
    )


def render_overlay(
    baseline_png: bytes,
    candidate_png: bytes,
    result: DiffResult,
) -> bytes:
    """Render a PNG with red rectangles around changed regions.
    
    Returns PNG bytes of the candidate with red overlays.
    """
    Image, np = _require_pil_numpy()
    
    with Image.open(io.BytesIO(candidate_png)) as img:
        # Ensure RGBA mode for drawing
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        
        # Create a drawing layer
        draw_img = img.copy()
        from PIL import ImageDraw
        
        draw = ImageDraw.Draw(draw_img)
        
        # Draw red rectangles around regions
        for region in result.regions:
            # Create outline effect: red border with slight transparency
            draw.rectangle(
                [region.x, region.y, region.x + region.width - 1, region.y + region.height - 1],
                outline=(255, 0, 0, 255),
                width=2,
            )
        
        # Save to bytes
        output = io.BytesIO()
        draw_img.save(output, format='PNG')
        return output.getvalue()


def _bytes_to_png(width: int, height: int, rgba_data: bytes) -> bytes:
    """Create a minimal valid PNG from raw RGBA bytes.
    
    For test-only use to generate baseline images without external dependencies.
    Uses a simple uncompressed PNG with no interlacing.
    """
    import zlib
    
    def make_chunk(chunk_type: bytes, data: bytes) -> bytes:
        """Create a PNG chunk with length, type, data, and CRC."""
        length = len(data)
        crc = zlib.crc32(chunk_type + data) & 0xffffffff
        return struct.pack('>I', length) + chunk_type + data + struct.pack('>I', crc)
    
    # IHDR chunk
    ihdr_data = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    ihdr = make_chunk(b'IHDR', ihdr_data)
    
    # IDAT chunk - compressed image data
    # Each scanline starts with filter byte 0
    scanlines = b''
    for y in range(height):
        scanlines += b'\x00'  # filter type: none
        scanlines += rgba_data[y * width * 4:(y + 1) * width * 4]
    
    compressed = zlib.compress(scanlines, 9)
    idat = make_chunk(b'IDAT', compressed)
    
    # IEND chunk
    iend = make_chunk(b'IEND', b'')
    
    # PNG signature
    signature = b'\x89PNG\r\n\x1a\n'
    
    return signature + ihdr + idat + iend


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        prog="codex-visual-diff",
        description="Pixel-level screenshot comparison",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # diff command
    diff_parser = subparsers.add_parser("diff", help="Compare two images")
    diff_parser.add_argument("baseline", type=Path, help="Baseline PNG file")
    diff_parser.add_argument("candidate", type=Path, help="Candidate PNG file")
    diff_parser.add_argument("--tolerance", "-t", type=int, default=0, help="Color tolerance")
    diff_parser.add_argument("--cell", "-c", type=int, default=16, help="Region grouping cell size")
    diff_parser.add_argument("--max-regions", "-m", type=int, default=20, help="Max regions to report")
    diff_parser.add_argument("--json", action="store_true", help="Output as JSON")
    diff_parser.add_argument("--overlay", type=Path, help="Write overlay PNG")
    
    args = parser.parse_args()
    
    if args.command == "diff":
        baseline = args.baseline.read_bytes()
        candidate = args.candidate.read_bytes()
        
        result = diff_images(
            baseline,
            candidate,
            tolerance=args.tolerance,
            cell=args.cell,
            max_regions=args.max_regions,
        )
        
        if args.json:
            output = {
                "identical": result.identical,
                "size_mismatch": result.size_mismatch,
                "width": result.width,
                "height": result.height,
                "changed_pixels": result.changed_pixels,
                "changed_ratio": result.changed_ratio,
                "max_channel_delta": result.max_channel_delta,
                "regions": [
                    {
                        "x": r.x,
                        "y": r.y,
                        "width": r.width,
                        "height": r.height,
                        "changed_pixels": r.changed_pixels,
                    }
                    for r in result.regions
                ],
            }
            print(json.dumps(output, indent=2, sort_keys=True))
        else:
            print(f"Size: {result.width}x{result.height}")
            print(f"Identical: {result.identical}")
            if result.size_mismatch:
                print("Size mismatch: images have different dimensions")
            print(f"Changed pixels: {result.changed_pixels} ({result.changed_ratio * 100:.2f}%)")
            print(f"Max channel delta: {result.max_channel_delta}")
            if result.regions:
                print(f"Regions ({len(result.regions)}):")
                for r in result.regions:
                    print(f"  ({r.x}, {r.y}) {r.width}x{r.height}: {r.changed_pixels} pixels")
        
        if args.overlay:
            overlay = render_overlay(baseline, candidate, result)
            args.overlay.write_bytes(overlay)
            print(f"Overlay written to {args.overlay}")
        
        sys.exit(0 if result.identical else 1)
