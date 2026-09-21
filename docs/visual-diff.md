# Visual Diff and Baseline Store

The visual diff system compares rendered screenshots against stored baselines to detect unintended visual changes.

## Overview

The system provides:
- **Pixel-level comparison** with configurable tolerance
- **Region grouping** to identify changed areas
- **Baseline storage** with metadata tracking
- **CLI tools** for baseline management and diffing

## Directory Layout

Baselines are stored under a root directory with this structure:

```
ROOT/
├── NAME1/
│   └── VIEWPORT1/
│       ├── viewport.png
│       └── viewport.json
└── NAME2/
    └── VIEWPORT2/
        ├── viewport.png
        └── viewport.json
```

Where:
- `NAME` is a baseline identifier (e.g., `login-page`, `dashboard`)
- `VIEWPORT` is the dimensions (e.g., `1280x800`)

## Metadata Fields

Each baseline's `viewport.json` contains:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | Baseline identifier |
| `viewport` | string | Yes | Viewport dimensions |
| `sha256` | string | Yes | SHA256 of the PNG file |
| `width` | integer | Yes | Image width in pixels |
| `height` | integer | Yes | Image height in pixels |
| `renderer` | object | Yes | Renderer info (Playwright, Chromium versions) |
| `masks` | array | No | List of CSS selectors that were masked |
| `source` | string | No | Free-form source description |
| `created_at` | string | Yes | UTC ISO 8601 timestamp |

## Exit Codes

The CLI uses these exit codes:

| Code | Meaning |
|------|---------|
| 0 | Identical (no differences found) |
| 1 | Differences found |
| 2 | Usage error or data error |

## CLI Commands

### baseline-save

Save a baseline image:

```bash
python3 -m codex_local_harness.visual_cli baseline-save \
    ROOT NAME VIEWPORT PNG \
    --renderer-json RENDERER_JSON \
    [--masks SELECTOR1 SELECTOR2] \
    [--source DESCRIPTION] \
    [--overwrite]
```

### baseline-list

List all saved baselines:

```bash
python3 -m codex_local_harness.visual_cli baseline-list ROOT
```

### diff

Compare two PNG files:

```bash
python3 -m codex_local_harness.visual_cli diff \
    BASELINE.png CANDIDATE.png \
    [--tolerance N] \
    [--cell N] \
    [--max-regions N] \
    [--json] \
    [--overlay OUT.png]
```

### compare

Compare a candidate to a stored baseline:

```bash
python3 -m codex_local_harness.visual_cli compare \
    ROOT NAME VIEWPORT CANDIDATE.png \
    [--tolerance N] \
    [--renderer-json RENDERER_JSON] \
    [--json] \
    [--ignore X,Y,W,H]
```

## Parameters

### Tolerance

Pixels are considered changed only when any RGB channel differs by more than `tolerance`. Default is 0 (exact match).

### Cell Size

Changed pixels are grouped into `cell × cell` grid cells, then merged into regions. Larger cells produce coarser region grouping. Default is 16.

### Max Regions

The diff result includes at most `max_regions` regions, sorted by changed pixel count (descending), then by y-coordinate, then by x-coordinate.

### Ignore Rectangles

Rectangles specified as `x,y,w,h` are excluded from comparison. Multiple rectangles can be specified.

## Limitations

### Renderer Dependency

Pixel baselines are only valid for one renderer version and font setup. Changes to:
- Browser/engine version
- Font rendering engine
- Display scaling
- Font antialiasing settings

...can cause false positives even when layout is unchanged.

**Recommendation**: Pin renderer versions and document the exact setup used for baseline creation.

### Platform Differences

Rendering can differ between:
- Operating systems (Windows, macOS, Linux)
- Font availability
- Hardware acceleration settings

Baseline images should be created on the target platform and checked into version control.

### Alpha Channel

The alpha channel is ignored during comparison. Only RGB channels are compared.

## API Reference

### `diff_images(baseline_png, candidate_png, *, tolerance=0, cell=16, max_regions=20, ignore=())`

Compare two PNG images and return a `DiffResult`:

```python
@dataclass(frozen=True)
class DiffResult:
    identical: bool
    size_mismatch: bool
    width: int
    height: int
    changed_pixels: int
    changed_ratio: float  # 0..1
    regions: tuple[Region, ...]
    max_channel_delta: int


@dataclass(frozen=True)
class Region:
    x: int
    y: int
    width: int
    height: int
    changed_pixels: int
```

### `render_overlay(baseline_png, candidate_png, result) -> bytes`

Render a PNG with red rectangles around changed regions for visual inspection.

### `save_baseline(root, name, viewport, png, *, renderer, masks=(), source="", created_at=None, overwrite=False) -> Path`

Save a baseline with metadata. Raises `FileExistsError` if baseline exists and `overwrite=False`.

### `load_baseline(root, name, viewport) -> tuple[bytes, dict]`

Load a baseline and verify SHA256. Raises `ValueError` on mismatch or `FileNotFoundError` if missing.

### `list_baselines(root) -> list[tuple[str, str]]`

List all baselines as `(name, viewport)` tuples, sorted.

### `compare_to_baseline(root, name, viewport, candidate_png, *, renderer, tolerance=0, ignore=()) -> tuple[DiffResult, list[str]]`

Compare candidate to baseline. Returns diff result and a list of warnings (e.g., renderer mismatch).
