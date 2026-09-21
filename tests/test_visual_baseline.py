"""Tests for visual baseline store functionality."""
from __future__ import annotations

import io
import json
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone

import pytest

from codex_local_harness.visual_baseline import (
    compare_to_baseline,
    list_baselines,
    load_baseline,
    save_baseline,
    _NAME_PATTERN,
    _VIEWPORT_PATTERN,
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


def _bytes_to_png(width: int, height: int, rgba_data: bytes) -> bytes:
    """Create a minimal valid PNG from raw RGBA bytes."""
    import struct
    import zlib

    def make_chunk(chunk_type: bytes, data: bytes) -> bytes:
        length = len(data)
        crc = zlib.crc32(chunk_type + data) & 0xffffffff
        return struct.pack('>I', length) + chunk_type + data + struct.pack('>I', crc)

    ihdr_data = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    ihdr = make_chunk(b'IHDR', ihdr_data)

    scanlines = b''
    for y in range(height):
        scanlines += b'\x00'
        scanlines += rgba_data[y * width * 4:(y + 1) * width * 4]

    compressed = zlib.compress(scanlines, 9)
    idat = make_chunk(b'IDAT', compressed)
    iend = make_chunk(b'IEND', b'')
    signature = b'\x89PNG\r\n\x1a\n'

    return signature + ihdr + idat + iend


class TestValidateName:
    """Test name validation."""

    def test_valid_name(self):
        """Test valid names."""
        assert _NAME_PATTERN.match("test")
        assert _NAME_PATTERN.match("test_case")
        assert _NAME_PATTERN.match("test-case")
        assert _NAME_PATTERN.match("test123")
        assert _NAME_PATTERN.match("a")  # Single char
        assert _NAME_PATTERN.match("a" * 64)  # Max length

    def test_invalid_name_starts_with_underscore(self):
        """Test names starting with underscore are rejected."""
        assert not _NAME_PATTERN.match("_test")

    def test_invalid_name_with_slash(self):
        """Test names with slash are rejected."""
        assert not _NAME_PATTERN.match("test/path")

    def test_invalid_name_too_long(self):
        """Test names over 64 chars are rejected."""
        assert not _NAME_PATTERN.match("a" * 65)


class TestValidateViewport:
    """Test viewport validation."""

    def test_valid_viewports(self):
        """Test valid viewport strings."""
        assert _VIEWPORT_PATTERN.match("1280x800")
        assert _VIEWPORT_PATTERN.match("1920x1080")
        assert _VIEWPORT_PATTERN.match("100x100")
        assert _VIEWPORT_PATTERN.match("10000x10000")

    def test_invalid_viewport_missing_x(self):
        """Test viewports without x are rejected."""
        assert not _VIEWPORT_PATTERN.match("1280-800")

    def test_invalid_viewport_too_short(self):
        """Test viewports with dimensions too short are rejected."""
        assert not _VIEWPORT_PATTERN.match("1x1")
        assert not _VIEWPORT_PATTERN.match("9x9")

    def test_invalid_viewport_too_long(self):
        """Test viewports with dimensions too long are rejected."""
        assert not _VIEWPORT_PATTERN.match("100000x100000")


class TestSaveBaseline:
    """Test save_baseline function."""


    def test_save_baseline_success(self, tmp_path: Path):
        """Test successful baseline save."""
        png = _create_rgba_png(100, 100, (255, 0, 0, 255))
        renderer = {"name": "test", "version": "1.0"}

        baseline_dir = save_baseline(
            tmp_path,
            "test-image",
            "1280x800",
            png,
            renderer=renderer,
        )

        assert baseline_dir.is_dir()
        assert (baseline_dir / "viewport.png").exists()
        assert (baseline_dir / "viewport.json").exists()


    def test_save_baseline_metadata(self, tmp_path: Path):
        """Test saved metadata contains all required fields."""
        png = _create_rgba_png(50, 50, (0, 255, 0, 255))
        renderer = {"name": "playwright", "browser": "chromium", "version": "1.40.0"}

        save_baseline(
            tmp_path,
            "test-image",
            "1000x1000",
            png,
            renderer=renderer,
            masks=[".mask-1", ".mask-2"],
            source="test run",
            created_at="2026-01-01T00:00:00Z",
        )

        meta_path = tmp_path / "test-image" / "1000x1000" / "viewport.json"
        with meta_path.open() as f:
            meta = json.load(f)

        assert meta["name"] == "test-image"
        assert meta["viewport"] == "1000x1000"
        assert meta["renderer"] == renderer
        assert meta["masks"] == [".mask-1", ".mask-2"]
        assert meta["source"] == "test run"
        assert meta["created_at"] == "2026-01-01T00:00:00Z"
        assert "sha256" in meta


    def test_save_baseline_sha256_verified(self, tmp_path: Path):
        """Test that saved PNG's SHA256 matches metadata."""
        png = _create_rgba_png(50, 50, (0, 0, 255, 255))

        save_baseline(
            tmp_path,
            "test-image",
            "100x100",
            png,
            renderer={},
        )

        meta_path = tmp_path / "test-image" / "100x100" / "viewport.json"
        with meta_path.open() as f:
            meta = json.load(f)

        stored_png = (tmp_path / "test-image" / "100x100" / "viewport.png").read_bytes()
        import hashlib
        assert hashlib.sha256(stored_png).hexdigest() == meta["sha256"]


    def test_save_baseline_refuses_overwrite(self, tmp_path: Path):
        """Test that save refuses to overwrite existing baseline."""
        png = _create_rgba_png(100, 100, (255, 0, 0, 255))

        save_baseline(
            tmp_path,
            "test-image",
            "1280x800",
            png,
            renderer={},
        )

        # Try to save again
        with pytest.raises(FileExistsError):
            save_baseline(
                tmp_path,
                "test-image",
                "1280x800",
                png,
                renderer={},
            )


    def test_save_baseline_allows_overwrite(self, tmp_path: Path):
        """Test that save with overwrite=True replaces existing."""
        png1 = _create_rgba_png(100, 100, (255, 0, 0, 255))
        png2 = _create_rgba_png(100, 100, (0, 255, 0, 255))

        save_baseline(
            tmp_path,
            "test-image",
            "1280x800",
            png1,
            renderer={},
        )

        # Overwrite with flag
        baseline_dir = save_baseline(
            tmp_path,
            "test-image",
            "1280x800",
            png2,
            renderer={},
            overwrite=True,
        )

        assert baseline_dir.is_dir()

        # Verify content changed
        stored_png = (baseline_dir / "viewport.png").read_bytes()
        assert stored_png == png2


    def test_save_baseline_invalid_name(self, tmp_path: Path):
        """Test that invalid names are rejected."""
        png = _create_rgba_png(100, 100, (255, 0, 0, 255))

        with pytest.raises(ValueError, match="name must match"):
            save_baseline(tmp_path, "_invalid", "1280x800", png, renderer={})

        with pytest.raises(ValueError, match="name must match"):
            save_baseline(tmp_path, "has/slash", "1280x800", png, renderer={})


    def test_save_baseline_invalid_viewport(self, tmp_path: Path):
        """Test that invalid viewports are rejected."""
        png = _create_rgba_png(100, 100, (255, 0, 0, 255))

        with pytest.raises(ValueError, match="viewport must match"):
            save_baseline(tmp_path, "test", "1280-800", png, renderer={})

        with pytest.raises(ValueError, match="viewport must match"):
            save_baseline(tmp_path, "test", "9x9", png, renderer={})


    def test_save_baseline_path_traversal_rejected(self, tmp_path: Path):
        """Test that path traversal attempts are rejected."""
        png = _create_rgba_png(100, 100, (255, 0, 0, 255))

        with pytest.raises(ValueError, match="must match"):
            save_baseline(tmp_path, "../escaped", "1280x800", png, renderer={})

        with pytest.raises(ValueError, match="must match"):
            save_baseline(tmp_path, "test", "../../../escaped", png, renderer={})


class TestLoadBaseline:
    """Test load_baseline function."""


    def test_load_baseline_success(self, tmp_path: Path):
        """Test successful baseline load."""
        png = _create_rgba_png(100, 100, (255, 0, 0, 255))
        renderer = {"name": "test", "version": "1.0"}

        save_baseline(
            tmp_path,
            "test-image",
            "1280x800",
            png,
            renderer=renderer,
        )

        loaded_png, loaded_meta = load_baseline(tmp_path, "test-image", "1280x800")

        assert loaded_png == png
        assert loaded_meta["name"] == "test-image"
        assert loaded_meta["viewport"] == "1280x800"
        assert loaded_meta["renderer"] == renderer


    def test_load_baseline_sha256_mismatch_raises(self, tmp_path: Path):
        """Test that SHA256 mismatch raises ValueError."""
        png = _create_rgba_png(100, 100, (255, 0, 0, 255))

        save_baseline(
            tmp_path,
            "test-image",
            "1280x800",
            png,
            renderer={},
        )

        # Corrupt the PNG file
        png_path = tmp_path / "test-image" / "1280x800" / "viewport.png"
        png_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)

        with pytest.raises(ValueError, match="SHA256 mismatch"):
            load_baseline(tmp_path, "test-image", "1280x800")

    def test_load_baseline_missing_raises(self, tmp_path: Path):
        """Test that missing baseline raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            load_baseline(tmp_path, "nonexistent", "1280x800")


class TestListBaselines:
    """Test list_baselines function."""

    def test_list_empty_directory(self, tmp_path: Path):
        """Test listing empty directory returns empty list."""
        assert list_baselines(tmp_path) == []


    def test_list_with_baselines(self, tmp_path: Path):
        """Test listing returns sorted list of baselines."""
        png = _create_rgba_png(100, 100, (255, 0, 0, 255))

        # Create multiple baselines
        save_baseline(tmp_path, "alpha", "100x100", png, renderer={})
        save_baseline(tmp_path, "beta", "200x200", png, renderer={})
        save_baseline(tmp_path, "alpha", "200x200", png, renderer={})

        baselines = list_baselines(tmp_path)

        assert baselines == [
            ("alpha", "100x100"),
            ("alpha", "200x200"),
            ("beta", "200x200"),
        ]


    def test_list_ignores_invalid_entries(self, tmp_path: Path):
        """Test that invalid entries are ignored."""
        png = _create_rgba_png(100, 100, (255, 0, 0, 255))

        # Create a valid baseline
        save_baseline(tmp_path, "valid", "100x100", png, renderer={})

        # Create invalid entries
        (tmp_path / "_invalid").mkdir()
        (tmp_path / "valid" / "_invalid_viewport").mkdir()

        baselines = list_baselines(tmp_path)

        assert baselines == [("valid", "100x100")]


class TestCompareToBaseline:
    """Test compare_to_baseline function."""


    def test_compare_identical(self, tmp_path: Path):
        """Test comparing identical images."""
        png = _create_rgba_png(100, 100, (255, 0, 0, 255))

        save_baseline(
            tmp_path,
            "test-image",
            "1280x800",
            png,
            renderer={"name": "playwright", "version": "1.0"},
        )

        result, warnings = compare_to_baseline(
            tmp_path,
            "test-image",
            "1280x800",
            png,
            renderer={"name": "playwright", "version": "1.0"},
        )

        assert result.identical is True
        assert result.changed_pixels == 0
        assert warnings == []


    def test_compare_with_changes(self, tmp_path: Path):
        """Test detecting differences."""
        png = _create_rgba_png(100, 100, (255, 0, 0, 255))
        other_png = _create_rgba_png(100, 100, (0, 255, 0, 255))

        save_baseline(
            tmp_path,
            "test-image",
            "1280x800",
            png,
            renderer={"name": "playwright", "version": "1.0"},
        )

        result, warnings = compare_to_baseline(
            tmp_path,
            "test-image",
            "1280x800",
            other_png,
            renderer={"name": "playwright", "version": "1.0"},
        )

        assert result.identical is False
        assert result.changed_pixels > 0


    def test_compare_renderer_mismatch_warning(self, tmp_path: Path):
        """Test that renderer mismatch generates warning."""
        png = _create_rgba_png(100, 100, (255, 0, 0, 255))

        save_baseline(
            tmp_path,
            "test-image",
            "1280x800",
            png,
            renderer={"name": "playwright", "version": "1.0"},
        )

        result, warnings = compare_to_baseline(
            tmp_path,
            "test-image",
            "1280x800",
            png,
            renderer={"name": "playwright", "version": "2.0"},
        )

        assert result.identical is True  # Same image
        assert len(warnings) == 1
        assert "renderer mismatch" in warnings[0]


    def test_compare_size_mismatch(self, tmp_path: Path):
        """Test comparing images of different sizes."""
        png = _create_rgba_png(100, 100, (255, 0, 0, 255))
        other_png = _create_rgba_png(200, 200, (0, 255, 0, 255))

        save_baseline(
            tmp_path,
            "test-image",
            "1280x800",
            png,
            renderer={},
        )

        result, warnings = compare_to_baseline(
            tmp_path,
            "test-image",
            "1280x800",
            other_png,
            renderer={},
        )

        assert result.size_mismatch is True
        assert result.identical is False


class TestRegionOrdering:
    """Test region ordering determinism."""


    def test_regions_deterministic(self, tmp_path: Path):
        """Test that region ordering is deterministic."""
        baseline_data = b"\xff" * 40000
        candidate_data = bytearray(b"\xff" * 40000)

        # Create pattern with multiple regions
        for y in range(80, 90):
            for x in range(80, 90):
                candidate_data[(y * 100 + x) * 4] = 0

        for y in range(10, 30):
            for x in range(10, 30):
                candidate_data[(y * 100 + x) * 4] = 0

        baseline = _bytes_to_png(100, 100, baseline_data)
        candidate = _bytes_to_png(100, 100, bytes(candidate_data))

        save_baseline(
            tmp_path,
            "test-image",
            "1280x800",
            baseline,
            renderer={},
        )

        result, _ = compare_to_baseline(
            tmp_path,
            "test-image",
            "1280x800",
            candidate,
            renderer={},
        )

        # Verify ordering: larger region first
        assert len(result.regions) == 2
        assert result.regions[0].changed_pixels >= result.regions[1].changed_pixels
