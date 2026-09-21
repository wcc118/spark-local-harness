"""CLI for visual diff and baseline operations."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .visual_baseline import (
    compare_to_baseline,
    list_baselines,
    load_baseline,
    save_baseline,
)
from .visual_diff import DiffResult, diff_images, render_overlay


def _parse_renderer_json(path: Path | None) -> dict[str, Any]:
    """Parse renderer JSON file."""
    if path is None:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def cmd_baseline_save(args: argparse.Namespace) -> int:
    """Execute baseline-save command."""
    renderer = _parse_renderer_json(args.renderer_json)
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
    return 0


def cmd_baseline_list(args: argparse.Namespace) -> int:
    """Execute baseline-list command."""
    baselines = list_baselines(args.root)
    if baselines:
        for name, viewport in baselines:
            print(f"{name}/{viewport}")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    """Execute diff command."""
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
            "changed_pixels": result.changed_pixels,
            "changed_ratio": result.changed_ratio,
            "height": result.height,
            "identical": result.identical,
            "max_channel_delta": result.max_channel_delta,
            "regions": [
                {
                    "changed_pixels": r.changed_pixels,
                    "height": r.height,
                    "width": r.width,
                    "x": r.x,
                    "y": r.y,
                }
                for r in result.regions
            ],
            "size_mismatch": result.size_mismatch,
            "width": result.width,
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
        print(f"Overlay written to: {args.overlay}")

    # Exit codes: 0 identical, 1 differences found
    return 0 if result.identical else 1


def cmd_compare(args: argparse.Namespace) -> int:
    """Execute compare command."""
    candidate = args.candidate.read_bytes()

    # Parse renderer if provided
    renderer = _parse_renderer_json(args.renderer_json) if args.renderer_json else {}

    # Build ignore regions
    ignore: list[tuple[int, int, int, int]] = []
    if args.ignore:
        for region_str in args.ignore:
            parts = region_str.split(",")
            if len(parts) != 4:
                print(
                    f"Error: ignore region must be x,y,w,h, got: {region_str}",
                    file=sys.stderr,
                )
                return 2
            try:
                ignore.append((int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])))
            except ValueError:
                print(f"Error: invalid ignore region: {region_str}", file=sys.stderr)
                return 2

    result, warnings = compare_to_baseline(
        args.root,
        args.name,
        args.viewport,
        candidate,
        renderer=renderer,
        tolerance=args.tolerance,
        ignore=ignore,
    )

    if args.json:
        output = {
            "changed_pixels": result.changed_pixels,
            "changed_ratio": result.changed_ratio,
            "height": result.height,
            "identical": result.identical,
            "max_channel_delta": result.max_channel_delta,
            "regions": [
                {
                    "changed_pixels": r.changed_pixels,
                    "height": r.height,
                    "width": r.width,
                    "x": r.x,
                    "y": r.y,
                }
                for r in result.regions
            ],
            "size_mismatch": result.size_mismatch,
            "width": result.width,
            "warnings": warnings,
        }
        print(json.dumps(output, indent=2, sort_keys=True))
    else:
        print(f"Size: {result.width}x{result.height}")
        print(f"Identical: {result.identical}")
        if result.size_mismatch:
            print("Size mismatch: candidate has different dimensions than baseline")
        print(f"Changed pixels: {result.changed_pixels} ({result.changed_ratio * 100:.2f}%)")
        print(f"Max channel delta: {result.max_channel_delta}")
        if warnings:
            print("Warnings:")
            for w in warnings:
                print(f"  - {w}")
        if result.regions:
            print(f"Regions ({len(result.regions)}):")
            for r in result.regions:
                print(f"  ({r.x}, {r.y}) {r.width}x{r.height}: {r.changed_pixels} pixels")

    # Exit codes: 0 identical, 1 differences found
    return 0 if result.identical else 1


def main() -> int:
    """Main entry point for visual CLI."""
    parser = argparse.ArgumentParser(
        prog="codex-visual",
        description="Visual diff and baseline operations",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # baseline-save subcommand
    save_parser = subparsers.add_parser(
        "baseline-save",
        help="Save a baseline image",
    )
    save_parser.add_argument("root", type=Path, help="Root directory")
    save_parser.add_argument("name", help="Baseline name")
    save_parser.add_argument("viewport", help="Viewport (e.g., 1280x800)")
    save_parser.add_argument("png", type=Path, help="PNG file")
    save_parser.add_argument("--renderer-json", type=Path, required=True, help="Renderer JSON file")
    save_parser.add_argument("--masks", nargs="*", default=[], help="Masked CSS selectors")
    save_parser.add_argument("--source", default="", help="Source string")
    save_parser.add_argument("--overwrite", action="store_true", help="Allow overwrite")

    # baseline-list subcommand
    list_parser = subparsers.add_parser(
        "baseline-list",
        help="List saved baselines",
    )
    list_parser.add_argument("root", type=Path, help="Root directory")

    # diff subcommand
    diff_parser = subparsers.add_parser(
        "diff",
        help="Compare two PNG files",
    )
    diff_parser.add_argument("baseline", type=Path, help="Baseline PNG file")
    diff_parser.add_argument("candidate", type=Path, help="Candidate PNG file")
    diff_parser.add_argument("--tolerance", "-t", type=int, default=0, help="Color tolerance")
    diff_parser.add_argument("--cell", "-c", type=int, default=16, help="Region grouping cell size")
    diff_parser.add_argument("--max-regions", "-m", type=int, default=20, help="Max regions")
    diff_parser.add_argument("--json", action="store_true", help="Output as JSON")
    diff_parser.add_argument("--overlay", type=Path, help="Write overlay PNG")

    # compare subcommand
    compare_parser = subparsers.add_parser(
        "compare",
        help="Compare candidate to stored baseline",
    )
    compare_parser.add_argument("root", type=Path, help="Root directory")
    compare_parser.add_argument("name", help="Baseline name")
    compare_parser.add_argument("viewport", help="Viewport")
    compare_parser.add_argument("candidate", type=Path, help="Candidate PNG file")
    compare_parser.add_argument("--tolerance", "-t", type=int, default=0, help="Color tolerance")
    compare_parser.add_argument("--renderer-json", type=Path, help="Renderer JSON file")
    compare_parser.add_argument("--json", action="store_true", help="Output as JSON")
    compare_parser.add_argument("--ignore", nargs="*", help="Ignore regions (x,y,w,h)")

    args = parser.parse_args()

    try:
        if args.command == "baseline-save":
            return cmd_baseline_save(args)
        elif args.command == "baseline-list":
            return cmd_baseline_list(args)
        elif args.command == "diff":
            return cmd_diff(args)
        elif args.command == "compare":
            return cmd_compare(args)
        else:
            parser.print_help()
            return 2
    except FileNotFoundError as exc:
        print(f"Error: file not found: {exc.filename}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
