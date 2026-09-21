"""CLI for visual render operations."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from .visual_render import validate_name, validate_viewport_name, validate_url, validate_selector, render


def normalize_viewport(v: str) -> tuple[str, int, int]:
    """Parse viewport string WxH and return (name, width, height)."""
    if 'x' not in v:
        raise ValueError(f"Invalid viewport format: {v}. Expected WxH")
    parts = v.split('x')
    if len(parts) != 2:
        raise ValueError(f"Invalid viewport format: {v}. Expected WxH")
    width, height = int(parts[0]), int(parts[1])
    return v, width, height


def main() -> int:
    """Main entry point for visual render CLI."""
    parser = argparse.ArgumentParser(
        prog='codex-visual-render-cli',
        description='Render deterministic screenshots using Playwright'
    )
    subparsers = parser.add_subparsers(dest='command', required=True)
    
    # Render subcommand
    render_parser = subparsers.add_parser('render', help='Render a URL to screenshots')
    render_parser.add_argument('--url', required=True, help='URL to render')
    render_parser.add_argument('--name', required=True, help='Name for this render (used in output filenames)')
    render_parser.add_argument('--out', required=True, type=Path, help='Output directory')
    render_parser.add_argument('--viewport', action='append', default=[], help='Viewport size (WxH format)')
    render_parser.add_argument('--mask', action='append', default=[], help='CSS selector to mask (hide)')
    render_parser.add_argument('--json', action='store_true', help='Output JSON summary')
    
    # Check-determinism subcommand
    determinism_parser = subparsers.add_parser('check-determinism', help='Check render determinism across multiple runs')
    determinism_parser.add_argument('--url', required=True, help='URL to render')
    determinism_parser.add_argument('--name', required=True, help='Name for this render')
    determinism_parser.add_argument('--viewport', required=True, help='Viewport size (WxH format)')
    determinism_parser.add_argument('--mask', action='append', default=[], help='CSS selector to mask (hide)')
    determinism_parser.add_argument('--runs', type=int, default=10, help='Number of render runs')
    determinism_parser.add_argument('--json', action='store_true', help='Output JSON summary')
    
    args = parser.parse_args()
    
    try:
        if args.command == 'render':
            return cmd_render(args)
        elif args.command == 'check-determinism':
            return cmd_check_determinism(args)
        else:
            parser.print_help()
            return 1
    except ValueError as e:
        print(f"Validation error: {e}", file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(f"Runtime error: {e}", file=sys.stderr)
        return 3
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return 1


def cmd_render(args: argparse.Namespace) -> int:
    """Execute render command."""
    # Validate inputs
    if not validate_name(args.name):
        raise ValueError(f"Invalid name: {args.name}")
    if not validate_url(args.url):
        raise ValueError(f"Invalid URL: {args.url}")
    
    # Validate viewports
    viewports = []
    default_viewports = ['1280x800', '390x844']
    viewport_list = args.viewport if args.viewport else default_viewports
    
    for vp in viewport_list:
        vp_name, vp_width, vp_height = normalize_viewport(vp)
        viewports.append({
            'name': vp_name,
            'width': vp_width,
            'height': vp_height
        })
    
    # Validate masks
    for mask in args.mask:
        if not validate_selector(mask):
            raise ValueError(f"Invalid mask selector: {mask}")
    
    # Build spec
    spec = {
        'pages': [
            {'name': args.name, 'url': args.url}
        ],
        'viewports': viewports,
        'masks': args.mask,
        'timeout_ms': 30000
    }
    
    # Ensure output directory exists
    args.out.mkdir(parents=True, exist_ok=True)
    
    # Render
    result = render(spec, args.out)
    
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(f"Rendered {result.get('pages', 0)} pages")
        if result.get('blocked_urls'):
            print(f"Blocked {len(result['blocked_urls'])} external URLs")
    
    return 0


def cmd_check_determinism(args: argparse.Namespace) -> int:
    """Execute check-determinism command."""
    # Validate inputs
    if not validate_name(args.name):
        raise ValueError(f"Invalid name: {args.name}")
    if not validate_url(args.url):
        raise ValueError(f"Invalid URL: {args.url}")
    
    vp_name, vp_width, vp_height = normalize_viewport(args.viewport)
    viewport = {'name': vp_name, 'width': vp_width, 'height': vp_height}
    
    # Validate masks
    for mask in args.mask:
        if not validate_selector(mask):
            raise ValueError(f"Invalid mask selector: {mask}")
    
    # Collect unique hashes across runs
    png_hashes: set[str] = set()
    text_hashes: set[str] = set()
    aria_hashes: set[str] = set()
    
    temp_dirs: list[Path] = []
    
    try:
        for i in range(args.runs):
            temp_dir = Path(tempfile.mkdtemp(prefix=f"render-determinism-{i}-"))
            temp_dirs.append(temp_dir)
            
            spec = {
                'pages': [
                    {'name': f"{args.name}-run{i}", 'url': args.url}
                ],
                'viewports': [viewport],
                'masks': args.mask,
                'timeout_ms': 30000
            }
            
            try:
                result = render(spec, temp_dir)
                
                # Read and hash outputs
                png_path = temp_dir / f"{vp_name}.png"
                text_path = temp_dir / f"{vp_name}.text.txt"
                aria_path = temp_dir / f"{vp_name}.aria.txt"
                
                if png_path.exists():
                    import hashlib
                    with open(png_path, 'rb') as f:
                        png_hashes.add(hashlib.sha256(f.read()).hexdigest())
                
                if text_path.exists():
                    import hashlib
                    with open(text_path, 'r', encoding='utf-8') as f:
                        text_hashes.add(hashlib.sha256(f.read().encode('utf-8')).hexdigest())
                
                if aria_path.exists():
                    import hashlib
                    with open(aria_path, 'r', encoding='utf-8') as f:
                        aria_hashes.add(hashlib.sha256(f.read().encode('utf-8')).hexdigest())
                
            except Exception as e:
                print(f"Run {i} failed: {e}", file=sys.stderr)
        
        # Determine which artifact types differ
        differ_types = []
        if len(png_hashes) > 1:
            differ_types.append('png')
        if len(text_hashes) > 1:
            differ_types.append('text')
        if len(aria_hashes) > 1:
            differ_types.append('aria')
        
        output = {
            'runs': args.runs,
            'unique_png_hashes': len(png_hashes),
            'unique_text_hashes': len(text_hashes),
            'unique_aria_hashes': len(aria_hashes),
            'differing_artifacts': differ_types,
            'all_identical': len(differ_types) == 0
        }
        
        if args.json:
            print(json.dumps(output, sort_keys=True))
        else:
            print(f"Ran {args.runs} times")
            print(f"Unique PNG hashes: {len(png_hashes)}")
            print(f"Unique text hashes: {len(text_hashes)}")
            print(f"Unique aria hashes: {len(aria_hashes)}")
            if differ_types:
                print(f"Artifacts differ: {', '.join(differ_types)}")
                return 1
            else:
                print("All artifacts are identical across runs")
        
        return 0 if len(differ_types) == 0 else 1
    
    finally:
        # Clean up temp directories
        for temp_dir in temp_dirs:
            try:
                shutil.rmtree(temp_dir)
            except OSError:
                pass


if __name__ == '__main__':
    sys.exit(main())
