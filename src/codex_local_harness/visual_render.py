"""Visual render wrapper for Playwright-based screenshot capture."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


def validate_name(name: str) -> bool:
    """Validate name matches pattern ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$."""
    return bool(re.match(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$', name))


def validate_viewport_name(name: str) -> bool:
    """Validate viewport name matches pattern ^[0-9]{2,5}x[0-9]{2,5}$."""
    return bool(re.match(r'^[0-9]{2,5}x[0-9]{2,5}$', name))


def validate_url(url: str) -> bool:
    """Validate URL starts with allowed prefixes."""
    return url.startswith('http://127.0.0.1') or url.startswith('http://localhost') or url.startswith('file://')


def validate_selector(selector: str) -> bool:
    """Validate selector is non-empty string without control characters."""
    if not selector or not isinstance(selector, str):
        return False
    # Check for control characters (ASCII 0-31 and 127)
    for char in selector:
        if ord(char) < 32 or ord(char) == 127:
            return False
    return True


DEFAULT_VIEWPORTS: tuple[dict[str, Any], ...] = (
    {'name': '1280x800', 'width': 1280, 'height': 800},
    {'name': '390x844', 'width': 390, 'height': 844},
)


def validate_spec(spec: dict[str, Any]) -> list[str]:
    """Validate spec structure and return list of errors."""
    errors = []

    # Check required fields
    if 'pages' not in spec or not isinstance(spec['pages'], list):
        errors.append("Missing or invalid 'pages' field")
    else:
        for i, page in enumerate(spec['pages']):
            if not isinstance(page, dict):
                errors.append(f"Page {i} is not a dict")
                continue
            if 'name' not in page:
                errors.append(f"Page {i} missing 'name'")
            elif not validate_name(page['name']):
                errors.append(f"Invalid page name: {page['name']}")
            if 'url' not in page:
                errors.append(f"Page {i} missing 'url'")
            elif not validate_url(page['url']):
                errors.append(f"Invalid URL: {page['url']}")

    if 'viewports' not in spec or not isinstance(spec['viewports'], list):
        errors.append("Missing or invalid 'viewports' field")
    else:
        for i, vp in enumerate(spec['viewports']):
            if not isinstance(vp, dict):
                errors.append(f"Viewport {i} is not a dict")
                continue
            if 'name' not in vp:
                errors.append(f"Viewport {i} missing 'name'")
            elif not validate_viewport_name(vp['name']):
                errors.append(f"Invalid viewport name: {vp['name']}")
            if 'width' not in vp or not isinstance(vp['width'], int):
                errors.append(f"Viewport {i} missing or invalid 'width'")
            if 'height' not in vp or not isinstance(vp['height'], int):
                errors.append(f"Viewport {i} missing or invalid 'height'")

    # Validate masks if present
    if 'masks' in spec and isinstance(spec['masks'], list):
        for i, mask in enumerate(spec['masks']):
            if not validate_selector(mask):
                errors.append(f"Invalid mask selector: {mask}")

    return errors


def render(spec: dict[str, Any], out_dir: Path, *, visual_dir: Path | None = None, node: str = "node", timeout: float = 300.0) -> dict[str, Any]:
    """Render screenshots using the Node ESM script.

    Args:
        spec: Specification dict with pages, viewports, masks, timeout_ms
        out_dir: Output directory for rendered files
        visual_dir: Path to pinned Playwright directory (default: CODEX_LOCAL_VISUAL_DIR env or default)
        node: Node executable path
        timeout: Process timeout in seconds

    Returns:
        Parsed summary from the Node script

    Raises:
        ValueError: If spec validation fails
        RuntimeError: If Playwright is missing or Node returns non-zero exit code
    """
    # Default viewports when the spec does not name any
    if 'viewports' not in spec:
        spec = {**spec, 'viewports': [dict(v) for v in DEFAULT_VIEWPORTS]}

    # Validate spec
    errors = validate_spec(spec)
    if errors:
        raise ValueError(f"Invalid spec: {'; '.join(errors)}")

    # Resolve visual directory
    if visual_dir is None:
        visual_dir = Path(
            spec.get('visual_dir', '').strip()
            if isinstance(spec.get('visual_dir'), str)
            else ''
        )
        if not visual_dir or not visual_dir.is_absolute():
            # Try environment variable first, then default
            visual_dir_str = None
            import os
            visual_dir_str = os.environ.get('CODEX_LOCAL_VISUAL_DIR')
            if visual_dir_str:
                visual_dir = Path(visual_dir_str)
            else:
                visual_dir = Path('/home/connellwc83/.local/share/codex-local-harness/visual')

    # Check Playwright is installed
    package_json = visual_dir / 'package.json'
    if not package_json.exists():
        install_cmd = "npm install --ignore-scripts --save-exact playwright@1.63.0"
        raise RuntimeError(
            f"Playwright not found at {visual_dir}. "
            f"Install with: {install_cmd}"
        )

    # Check node_modules contains playwright
    node_modules = visual_dir / 'node_modules' / 'playwright'
    if not node_modules.exists():
        install_cmd = "npm install --ignore-scripts --save-exact playwright@1.63.0"
        raise RuntimeError(
            f"Playwright module not found at {visual_dir / 'node_modules' / 'playwright'}. "
            f"Install with: {install_cmd}"
        )

    # Create spec file
    import tempfile
    import os

    # Add visual_dir to spec for Node script
    spec_with_dir = dict(spec)
    spec_with_dir['visual_dir'] = str(visual_dir)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(spec_with_dir, f)
        spec_file = f.name

    try:
        # Build argument list for subprocess (no shell)
        cmd = [
            node,
            str(Path(__file__).resolve().parents[2] / "tools" / "visual" / "render.mjs"),
            '--spec', spec_file,
            '--out', str(out_dir)
        ]

        # Minimal explicit environment. HOME is required so Chromium finds its
        # browser cache; nothing else from the caller's environment is passed.
        env = {
            key: os.environ[key]
            for key in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR', 'PLAYWRIGHT_BROWSERS_PATH')
            if key in os.environ
        }
        env['CODEX_LOCAL_VISUAL_DIR'] = str(visual_dir)

        # Run the Node script
        result = subprocess.run(
            cmd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        # Check exit code
        if result.returncode == 2:
            # Bad spec - the Node script already printed JSON error
            try:
                stderr_data = json.loads(result.stderr.strip())
                raise ValueError(f"Bad spec: {stderr_data.get('message', 'unknown')}")
            except json.JSONDecodeError:
                raise ValueError(f"Bad spec: {result.stderr.strip()}")
        elif result.returncode == 3:
            # Playwright or browser cannot be launched
            try:
                stderr_data = json.loads(result.stderr.strip())
                raise RuntimeError(
                    f"Playwright or browser cannot be launched: {stderr_data.get('message', 'unknown')}. "
                    f"Install with: npm install --ignore-scripts --save-exact playwright@1.63.0"
                )
            except json.JSONDecodeError:
                raise RuntimeError(
                    f"Playwright or browser cannot be launched: {result.stderr.strip()}. "
                    f"Install with: npm install --ignore-scripts --save-exact playwright@1.63.0"
                )
        elif result.returncode == 4:
            # Page failure: never return a partial summary as if it were success
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(f"Page failure (Node exit 4): {detail}")
        elif result.returncode != 0:
            # Other error
            raise RuntimeError(
                f"Node script exited with code {result.returncode}. "
                f"stdout: {result.stdout.strip()}. "
                f"stderr: {result.stderr.strip()}"
            )

        # Parse and return summary
        try:
            summary = json.loads(result.stdout.strip())
            return summary
        except json.JSONDecodeError:
            raise RuntimeError(f"Failed to parse summary from Node script: {result.stdout.strip()}")

    finally:
        # Clean up spec file
        try:
            os.unlink(spec_file)
        except OSError:
            pass


def main() -> int:
    """CLI entry point for visual_render."""
    import argparse

    parser = argparse.ArgumentParser(
        prog='codex-visual-render',
        description='Render deterministic screenshots using Playwright'
    )
    parser.add_argument('--spec', '-s', type=Path, required=True, help='Spec JSON file')
    parser.add_argument('--out', '-o', type=Path, required=True, help='Output directory')
    parser.add_argument('--visual-dir', type=Path, help='Playwright pinned directory')
    parser.add_argument('--node', default='node', help='Node executable path')
    parser.add_argument('--timeout', type=float, default=300.0, help='Process timeout in seconds')

    args = parser.parse_args()

    try:
        # Load spec
        with args.spec.open('r', encoding='utf-8') as f:
            spec = json.load(f)

        # Render
        result = render(
            spec,
            args.out,
            visual_dir=args.visual_dir,
            node=args.node,
            timeout=args.timeout
        )

        print(json.dumps(result, sort_keys=True))
        return 0

    except ValueError as e:
        print(f"Validation error: {e}", file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(f"Runtime error: {e}", file=sys.stderr)
        return 3
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
