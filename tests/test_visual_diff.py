"""Tests for visual diff functionality."""
from __future__ import annotations

import io
import sys

import pytest

from codex_local_harness.visual_diff import (
    DiffResult,
    Region,
    diff_images,
    render_overlay,
    _bytes_to_png,
)

PIL = pytest.importorskip("PIL")
NP = pytest.importorskip("numpy")
from PIL import Image


def _create_rgba_png(width: int, height: int, color: tuple[int, int, int, int]) -> bytes:
    """Create a solid-color PNG."""
    img = Image.new("RGBA", (width, height), color)
    output = io.BytesIO()
    img.save(output, format="PNG")
    return output.getvalue()


def test_diff_identical_images():
    """Test comparing identical images returns identical=True."""
    png = _create_rgba_png(100, 100, (255, 128, 64, 255))
    result = diff_images(png, png)

    assert result.identical is True
    assert result.size_mismatch is False
    assert result.width == 100
    assert result.height == 100
    assert result.changed_pixels == 0
    assert result.changed_ratio == 0.0
    assert result.max_channel_delta == 0
    assert result.regions == ()



def test_diff_single_pixel_change():
    """Test detecting a single pixel change."""
    # Baseline: all white
    baseline = _bytes_to_png(100, 100, b"\xff" * 40000)

    # Candidate: same except one pixel
    candidate_data = bytearray(b"\xff" * 40000)
    candidate_data[50 * 4 + 50] = 0  # Change one R channel
    candidate = bytes(candidate_data)
    candidate = _bytes_to_png(100, 100, candidate)

    result = diff_images(baseline, candidate)

    assert result.identical is False
    assert result.size_mismatch is False
    assert result.changed_pixels == 1
    assert result.changed_ratio == 1 / 10000
    assert result.max_channel_delta == 255
    assert len(result.regions) == 1
    assert result.regions[0].x == 50
    assert result.regions[0].y == 50
    assert result.regions[0].width == 1
    assert result.regions[0].height == 1



def test_diff_tolerance_boundary():
    """Test that tolerance works at the boundary."""
    # Create images with exactly tolerance difference
    tol = 5

    # Baseline: all white
    baseline = _bytes_to_png(50, 50, b"\xff" * 10000)

    # Candidate: pixel differs by exactly tolerance
    candidate_data = bytearray(b"\xff" * 10000)
    candidate_data[0] = 255 - tol  # Exactly at tolerance
    candidate = _bytes_to_png(50, 50, bytes(candidate_data))

    result = diff_images(baseline, candidate, tolerance=tol)
    assert result.identical is True
    assert result.changed_pixels == 0

    # Now just over tolerance
    candidate_data[0] = 255 - tol - 1
    candidate = _bytes_to_png(50, 50, bytes(candidate_data))

    result = diff_images(baseline, candidate, tolerance=tol)
    assert result.identical is False
    assert result.changed_pixels >= 1



def test_diff_alpha_ignored():
    """Test that alpha channel changes are ignored."""
    # Baseline: fully opaque
    baseline = _bytes_to_png(50, 50, b"\xff\x00\x00\xff" * 2500)

    # Candidate: same but alpha changed
    candidate_data = bytearray(b"\xff\x00\x00\xff" * 2500)
    candidate_data[3] = 0  # Change alpha only
    candidate = _bytes_to_png(50, 50, bytes(candidate_data))

    result = diff_images(baseline, candidate)
    assert result.identical is True


def test_diff_size_mismatch():
    """Test that size mismatches are detected."""
    baseline = _bytes_to_png(100, 100, b"\xff" * 40000)
    candidate = _bytes_to_png(200, 100, b"\xff" * 80000)

    result = diff_images(baseline, candidate)

    assert result.identical is False
    assert result.size_mismatch is True
    assert result.width == 200
    assert result.height == 100
    assert result.changed_pixels == 30000  # 10000 + 20000
    assert result.changed_ratio == 1.0
    assert result.regions == ()



def test_diff_ignore_rectangles():
    """Test that ignore rectangles exclude regions from comparison."""
    # Create 100x100 images
    baseline = _bytes_to_png(100, 100, b"\xff" * 40000)
    candidate_data = bytearray(b"\xff" * 40000)

    # Change pixels in multiple locations
    for y in range(10, 20):
        for x in range(10, 20):
            candidate_data[(y * 100 + x) * 4] = 0  # Top-left region

    for y in range(50, 60):
        for x in range(50, 60):
            candidate_data[(y * 100 + x) * 4] = 0  # Center region

    candidate = _bytes_to_png(100, 100, bytes(candidate_data))

    # Without ignore: 200 changed pixels
    result = diff_images(baseline, candidate)
    assert result.changed_pixels == 200

    # With ignore on center region: only 100 changed
    result = diff_images(baseline, candidate, ignore=[(50, 50, 10, 10)])
    assert result.changed_pixels == 100
    assert len(result.regions) == 1



def test_diff_region_grouping():
    """Test that changed pixels are grouped into regions."""
    # Create images with scattered changed pixels
    baseline = _bytes_to_png(100, 100, b"\xff" * 40000)
    candidate_data = bytearray(b"\xff" * 40000)

    # Add multiple disconnected changed pixels
    for y in range(0, 100, 20):
        for x in range(0, 100, 20):
            candidate_data[(y * 100 + x) * 4] = 0

    candidate = _bytes_to_png(100, 100, bytes(candidate_data))

    result = diff_images(baseline, candidate, cell=16, max_regions=20)

    # Should have 25 changed pixels in 16x16 cells
    assert result.changed_pixels == 25
    # With cell=16, each changed pixel might be in its own cell
    # But we limit to max_regions=20
    assert len(result.regions) <= 20



def test_diff_deterministic_ordering():
    """Test that region ordering is deterministic."""
    baseline = _bytes_to_png(100, 100, b"\xff" * 40000)

    # Create a pattern with multiple regions
    candidate_data = bytearray(b"\xff" * 40000)

    # Small region at (80, 80) - should come after larger ones
    for y in range(80, 85):
        for x in range(80, 85):
            candidate_data[(y * 100 + x) * 4] = 0

    # Larger region at (10, 10)
    for y in range(10, 30):
        for x in range(10, 30):
            candidate_data[(y * 100 + x) * 4] = 0

    # Small region at (50, 10) - same y as larger region
    for y in range(50, 55):
        for x in range(50, 55):
            candidate_data[(y * 100 + x) * 4] = 0

    candidate = _bytes_to_png(100, 100, bytes(candidate_data))

    # Run diff twice and verify same result
    result1 = diff_images(baseline, candidate, cell=16, max_regions=20)
    result2 = diff_images(baseline, candidate, cell=16, max_regions=20)

    assert result1.regions == result2.regions

    # Verify ordering: larger regions first, then by y, then by x
    for i in range(len(result1.regions) - 1):
        r1, r2 = result1.regions[i], result1.regions[i + 1]
        if r1.changed_pixels == r2.changed_pixels:
            if r1.y == r2.y:
                assert r1.x <= r2.x, "x should be ascending for same y"
            assert r1.y <= r2.y, "y should be ascending"



def test_render_overlay_valid_png():
    """Test that render_overlay produces a valid PNG."""
    Image, _ = _require_pil_numpy()

    baseline = _bytes_to_png(100, 100, b"\xff" * 40000)
    candidate_data = bytearray(b"\xff" * 40000)
    candidate_data[5000] = 0  # One changed pixel
    candidate = _bytes_to_png(100, 100, bytes(candidate_data))

    result = diff_images(baseline, candidate)
    overlay = render_overlay(baseline, candidate, result)

    # Verify it's a valid PNG
    with Image.open(io.BytesIO(overlay)) as img:
        assert img.mode == "RGBA"
        assert img.size == (100, 100)



def test_render_overlay_has_rectangles():
    """Test that render_overlay draws rectangles around regions."""
    Image, _ = _require_pil_numpy()

    baseline = _bytes_to_png(100, 100, b"\xff" * 40000)
    candidate_data = bytearray(b"\xff" * 40000)

    # Make a 10x10 block changed
    for y in range(10, 20):
        for x in range(10, 20):
            candidate_data[(y * 100 + x) * 4] = 0

    candidate = _bytes_to_png(100, 100, bytes(candidate_data))

    result = diff_images(baseline, candidate, cell=16)
    overlay = render_overlay(baseline, candidate, result)

    with Image.open(io.BytesIO(overlay)) as img:
        # The overlay should have red pixels around the changed region
        # Check a corner of the region
        pixel = img.getpixel((10, 10))
        # Should be different from baseline (white)
        assert pixel != (255, 255, 255, 255)



def test_diff_max_regions_limit():
    """Test that max_regions limits the number of regions returned."""
    baseline = _bytes_to_png(200, 200, b"\xff" * 160000)
    candidate_data = bytearray(b"\xff" * 160000)

    # Create 50 small regions
    for i in range(50):
        for y in range(i * 4, i * 4 + 2):
            for x in range(i * 4, i * 4 + 2):
                candidate_data[(y * 200 + x) * 4] = 0

    candidate = _bytes_to_png(200, 200, bytes(candidate_data))

    result = diff_images(baseline, candidate, cell=8, max_regions=10)

    assert len(result.regions) <= 10
    assert result.changed_pixels == 200  # 50 * 4



def test_diff_empty_image():
    """Test diffing empty/very small images."""
    # 1x1 images
    baseline = _bytes_to_png(1, 1, b"\xff\x00\x00\xff")
    candidate = _bytes_to_png(1, 1, b"\xff\x00\x00\xff")

    result = diff_images(baseline, candidate)
    assert result.identical is True
    assert result.changed_pixels == 0
