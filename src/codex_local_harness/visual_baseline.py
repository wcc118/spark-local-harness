"""Baseline store and comparison for visual regression testing."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if sys.version_info < (3, 11):
    from typing_extensions import Self
else:
    from typing import Self

from .visual_diff import DiffResult, diff_images


_NAME_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')
_VIEWPORT_PATTERN = re.compile(r'^[0-9]{2,5}x[0-9]{2,5}$')


def _validate_name(name: str) -> None:
    """Validate baseline name against the naming pattern."""
    if not name:
        raise ValueError("name must be non-empty")
    if not _NAME_PATTERN.match(name):
        raise ValueError(
            f"name must match ^[A-Za-z0-9][A-Za-z0-9._-]{{0,63}}$, got: {name}"
        )


def _validate_viewport(viewport: str) -> None:
    """Validate viewport string against the viewport pattern."""
    if not viewport:
        raise ValueError("viewport must be non-empty")
    if not _VIEWPORT_PATTERN.match(viewport):
        raise ValueError(
            f"viewport must match ^[0-9]{{2,5}}x[0-9]{{2,5}}$, got: {viewport}"
        )


def _validate_path_component(path: str) -> None:
    """Validate path component to prevent path traversal."""
    if '..' in path or '/' in path or '\\' in path:
        raise ValueError(f"invalid path component: {path}")


def _png_dimensions(data: bytes) -> tuple[int, int]:
    """Return (width, height) from a PNG header without decoding the image."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("data is not a valid PNG image")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    if width < 1 or height < 1:
        raise ValueError("PNG has invalid dimensions")
    return width, height


def _compute_sha256(data: bytes) -> str:
    """Compute SHA256 hex digest of bytes."""
    return hashlib.sha256(data).hexdigest()


def save_baseline(
    root: Path | str,
    name: str,
    viewport: str,
    png: bytes,
    *,
    renderer: dict[str, Any],
    masks: Sequence[str] = (),
    source: str = "",
    created_at: str | None = None,
    overwrite: bool = False,
) -> Path:
    """Save a baseline image with metadata.

    Args:
        root: Root directory for baseline storage.
        name: Baseline name (identifier).
        viewport: Viewport dimensions (e.g., "1280x800").
        png: Raw PNG bytes.
        renderer: Free-form dict with renderer info (e.g., Playwright/Chromium versions).
        masks: List of CSS selectors that were masked.
        source: Free-form source string.
        created_at: UTC ISO 8601 timestamp. Defaults to now.
        overwrite: If True, allow overwriting existing baselines.

    Returns:
        Path to the saved baseline directory.

    Raises:
        ValueError: If name or viewport is invalid, or if path traversal detected.
        FileExistsError: If baseline already exists and overwrite=False.
    """
    root = Path(root)

    # Validate inputs
    _validate_name(name)
    _validate_viewport(viewport)
    _validate_path_component(name)
    _validate_path_component(viewport)

    # Reject non-PNG input before touching the filesystem
    width, height = _png_dimensions(png)

    # Build paths
    baseline_dir = root / name / viewport
    png_path = baseline_dir / "viewport.png"
    meta_path = baseline_dir / "viewport.json"

    # Check for existing baseline
    if not overwrite and (png_path.exists() or meta_path.exists()):
        raise FileExistsError(f"Baseline already exists: {baseline_dir}")

    # Create directory
    baseline_dir.mkdir(parents=True, exist_ok=True)

    # Compute SHA256 of PNG
    sha256 = _compute_sha256(png)

    # Determine created_at
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()

    # Metadata dict
    metadata = {
        "created_at": created_at,
        "height": height,
        "masks": list(masks),
        "name": name,
        "renderer": renderer,
        "sha256": sha256,
        "source": source,
        "viewport": viewport,
        "width": width,
    }

    # Write metadata atomically
    meta_fd, meta_temp = tempfile.mkstemp(
        dir=baseline_dir,
        prefix=".viewport.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(meta_fd, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(meta_temp, meta_path)
    except Exception:
        os.unlink(meta_temp)
        raise

    # Write PNG atomically
    png_fd, png_temp = tempfile.mkstemp(
        dir=baseline_dir,
        prefix=".viewport.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(png_fd, "wb") as f:
            f.write(png)
        os.replace(png_temp, png_path)
    except Exception:
        os.unlink(png_temp)
        raise

    return baseline_dir


def load_baseline(root: Path | str, name: str, viewport: str) -> tuple[bytes, dict[str, Any]]:
    """Load a baseline image and its metadata.

    Args:
        root: Root directory for baseline storage.
        name: Baseline name.
        viewport: Viewport dimensions.

    Returns:
        Tuple of (png_bytes, metadata_dict).

    Raises:
        FileNotFoundError: If baseline files don't exist.
        ValueError: If SHA256 doesn't match or path traversal detected.
    """
    root = Path(root)

    # Validate inputs
    _validate_name(name)
    _validate_viewport(viewport)
    _validate_path_component(name)
    _validate_path_component(viewport)

    # Build paths
    baseline_dir = root / name / viewport
    png_path = baseline_dir / "viewport.png"
    meta_path = baseline_dir / "viewport.json"

    # Check existence
    if not png_path.exists() or not meta_path.exists():
        raise FileNotFoundError(f"Baseline not found: {baseline_dir}")

    # Load metadata
    with meta_path.open("r", encoding="utf-8") as f:
        metadata = json.load(f)

    # Load PNG
    png = png_path.read_bytes()

    # Verify SHA256
    stored_sha256 = metadata.get("sha256")
    if stored_sha256 is None:
        raise ValueError("metadata missing sha256 field")

    computed_sha256 = _compute_sha256(png)
    if computed_sha256 != stored_sha256:
        raise ValueError(
            f"SHA256 mismatch: expected {stored_sha256}, got {computed_sha256}"
        )

    width, height = _png_dimensions(png)
    if metadata.get("width") != width or metadata.get("height") != height:
        raise ValueError(
            f"metadata dimensions {metadata.get('width')}x{metadata.get('height')} "
            f"do not match PNG {width}x{height}"
        )

    return png, metadata


def list_baselines(root: Path | str) -> list[tuple[str, str]]:
    """List all saved baselines in the root directory.

    Args:
        root: Root directory for baseline storage.

    Returns:
        Sorted list of (name, viewport) tuples.
    """
    root = Path(root)

    baselines: list[tuple[str, str]] = []

    if not root.exists():
        return []

    # Scan name directories
    for name_dir in sorted(root.iterdir()):
        if not name_dir.is_dir():
            continue

        name = name_dir.name
        try:
            _validate_name(name)
        except ValueError:
            continue

        # Scan viewport files within
        for viewport_dir in sorted(name_dir.iterdir()):
            if not viewport_dir.is_dir():
                continue

            viewport = viewport_dir.name
            try:
                _validate_viewport(viewport)
            except ValueError:
                continue

            # Verify both files exist
            png_path = viewport_dir / "viewport.png"
            meta_path = viewport_dir / "viewport.json"

            if png_path.exists() and meta_path.exists():
                baselines.append((name, viewport))

    return sorted(baselines)


def compare_to_baseline(
    root: Path | str,
    name: str,
    viewport: str,
    candidate_png: bytes,
    *,
    renderer: dict[str, Any],
    tolerance: int = 0,
    ignore: Sequence[tuple[int, int, int, int]] = (),
) -> tuple[DiffResult, list[str]]:
    """Compare a candidate image to a stored baseline.

    Args:
        root: Root directory for baseline storage.
        name: Baseline name.
        viewport: Viewport dimensions.
        candidate_png: Raw PNG bytes of the candidate image.
        renderer: Renderer info dict for comparison.
        tolerance: Color tolerance for diff.
        ignore: Sequence of (x, y, w, h) rectangles to exclude.

    Returns:
        Tuple of (DiffResult, list of human-readable warnings).
    """
    root = Path(root)

    # Load baseline
    baseline_png, metadata = load_baseline(root, name, viewport)

    warnings: list[str] = []

    # Check renderer mismatch
    stored_renderer = metadata.get("renderer", {})
    if stored_renderer != renderer:
        warnings.append(
            "renderer mismatch: stored renderer differs from current renderer; "
            "pixel baselines may be invalid"
        )

    # Run diff
    result = diff_images(
        baseline_png,
        candidate_png,
        tolerance=tolerance,
        ignore=ignore,
    )

    return result, warnings


if __name__ == "__main__":
    import sys
    from argparse import ArgumentParser

    parser = ArgumentParser(
        prog="codex-visual-baseline",
        description="Baseline store operations",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # save command
    save_parser = subparsers.add_parser("save", help="Save a baseline")
    save_parser.add_argument("root", type=Path, help="Root directory")
    save_parser.add_argument("name", help="Baseline name")
    save_parser.add_argument("viewport", help="Viewport (e.g., 1280x800)")
    save_parser.add_argument("png", type=Path, help="PNG file")
    save_parser.add_argument("--renderer-json", type=Path, required=True, help="Renderer JSON file")
    save_parser.add_argument("--masks", nargs="*", default=[], help="Masked CSS selectors")
    save_parser.add_argument("--source", default="", help="Source string")
    save_parser.add_argument("--overwrite", action="store_true", help="Allow overwrite")

    # list command
    list_parser = subparsers.add_parser("list", help="List baselines")
    list_parser.add_argument("root", type=Path, help="Root directory")

    args = parser.parse_args()

    if args.command == "save":
        renderer = json.loads(args.renderer_json.read_text(encoding="utf-8"))
        png = args.png.read_bytes()

        baseline_dir = save_baseline(
            args.root,
            args.name,
            args.viewport,
            png,
            renderer=renderer,
            masks=args.masks,
            source=args.source,
            overwrite=args.overwrite,
        )

        print(f"Saved baseline to: {baseline_dir}")
        sys.exit(0)

    elif args.command == "list":
        baselines = list_baselines(args.root)
        if baselines:
            for name, viewport in baselines:
                print(f"{name}/{viewport}")
        sys.exit(0)
