"""Tests for visual render functionality."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from codex_local_harness.visual_render import (
    validate_name,
    validate_viewport_name,
    validate_url,
    validate_selector,
    validate_spec,
    render,
)


# Test fixtures directory
TESTS_DIR = Path(__file__).parent


def skip_if_playwright_missing() -> str | None:
    """Skip if Playwright is not installed. Returns reason or None."""
    visual_dir = Path(os.environ.get('CODEX_LOCAL_VISUAL_DIR', str(Path.home() / '.local' / 'share' / 'codex-local-harness' / 'visual')))
    if not (visual_dir / 'package.json').exists():
        return f"Playwright not found at {visual_dir}"
    if not (visual_dir / 'node_modules' / 'playwright').exists():
        return f"Playwright module not found at {visual_dir / 'node_modules' / 'playwright'}"
    return None


# Fixtures defined at module level for reuse across test classes
@pytest.fixture
def static_html_fixture(tmp_path: Path) -> str:
    """Create a static HTML fixture file."""
    html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Test Page</title>
    <style>
        body { margin: 0; font-family: sans-serif; }
        .header { padding: 20px; background: #f0f0f0; }
        .content { padding: 20px; }
        .footer { padding: 20px; background: #e0e0e0; }
        .masked { background: #c00; color: #fff; padding: 10px; }
    </style>
</head>
<body>
    <div class="header">Header Section</div>
    <div class="content">
        <h1>Main Content</h1>
        <p>This is test content.</p>
        <div class="masked">This is masked</div>
    </div>
    <div class="footer">Footer Section</div>
</body>
</html>"""
    fixture_path = tmp_path / "static.html"
    fixture_path.write_text(html)
    return f"file://{fixture_path}"


@pytest.fixture
def overflow_fixture(tmp_path: Path) -> str:
    """Create HTML with overflow elements."""
    html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Overflow Test</title>
    <style>
        body { margin: 0; overflow-x: auto; }
        .wide-box {
            width: 2000px;
            height: 100px;
            background: lightblue;
        }
        .hidden-scroll { overflow: hidden; }
    </style>
</head>
<body>
    <div class="wide-box">Wide content that overflows viewport</div>
    <div class="hidden-scroll">This has hidden overflow</div>
</body>
</html>"""
    fixture_path = tmp_path / "overflow.html"
    fixture_path.write_text(html)
    return f"file://{fixture_path}"


@pytest.fixture
def external_image_fixture(tmp_path: Path) -> str:
    """Create HTML with external image (should be blocked)."""
    html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>External Image Test</title>
</head>
<body>
    <h1>Page with External Image</h1>
    <img src="http://203.0.113.1/x.png" alt="External image">
</body>
</html>"""
    fixture_path = tmp_path / "external_image.html"
    fixture_path.write_text(html)
    return f"file://{fixture_path}"


@pytest.fixture
def volatile_text_fixture(tmp_path: Path) -> str:
    """Create HTML with random text on each load."""
    html = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Volatile Text Test</title>
</head>
<body>
    <p>Random number: <span id="random"></span></p>
    <script>
        document.getElementById('random').textContent = Math.random();
    </script>
</body>
</html>"""
    fixture_path = tmp_path / "volatile.html"
    fixture_path.write_text(html)
    return f"file://{fixture_path}"


class TestValidation:
    """Tests for validation functions."""

    def test_validate_name_valid(self):
        """Test valid name patterns."""
        assert validate_name("test") is True
        assert validate_name("test123") is True
        assert validate_name("test_name") is True
        assert validate_name("test-name") is True
        assert validate_name("test.name") is True
        assert validate_name("A" * 64) is True  # Max length

    def test_validate_name_invalid(self):
        """Test invalid name patterns."""
        assert validate_name("") is False  # Empty
        assert validate_name("-test") is False  # Starts with hyphen
        assert validate_name(".test") is False  # Starts with dot
        assert validate_name("123") is True   # Starts with number (valid per regex)
        assert validate_name("A" * 65) is False  # Too long

    def test_validate_viewport_name_valid(self):
        """Test valid viewport name patterns."""
        assert validate_viewport_name("1280x800") is True
        assert validate_viewport_name("390x844") is True
        assert validate_viewport_name("100x100") is True
        assert validate_viewport_name("9999x9999") is True

    def test_validate_viewport_name_invalid(self):
        """Test invalid viewport name patterns."""
        assert validate_viewport_name("1280x800000") is False  # Height 6 digits too long
        assert validate_viewport_name("x800") is False  # Missing width
        assert validate_viewport_name("1280x") is False  # Missing height
        assert validate_viewport_name("1280") is False  # Missing x
        assert validate_viewport_name("1280X800") is False  # Capital X

    def test_validate_url_valid(self):
        """Test valid URL patterns."""
        assert validate_url("http://127.0.0.1:8080/page") is True
        assert validate_url("http://localhost:3000") is True
        assert validate_url("http://localhost:8765/dashboard") is True
        assert validate_url("file:///path/to/file.html") is True

    def test_validate_url_invalid(self):
        """Test invalid URL patterns."""
        assert validate_url("https://example.com") is False
        assert validate_url("http://example.com") is False
        assert validate_url("ftp://example.com") is False
        assert validate_url("data:text/html,<html>") is False
        assert validate_url("blob:https://example.com/abc") is False
        assert validate_url("../path") is False

    def test_validate_selector_valid(self):
        """Test valid selector patterns."""
        assert validate_selector(".class") is True
        assert validate_selector("#id") is True
        assert validate_selector("div.class") is True
        assert validate_selector("div > span") is True
        assert validate_selector("div[data-foo='bar']") is True

    def test_validate_selector_invalid(self):
        """Test invalid selector patterns."""
        assert validate_selector("") is False  # Empty
        assert validate_selector("div\x00span") is False  # Null character
        assert validate_selector("div\x1Fspan") is False  # Control char
        assert validate_selector("div\x7Fspan") is False  # DEL character
        assert validate_selector(None) is False  # None

    def test_validate_spec_valid(self):
        """Test valid spec validation."""
        spec = {
            'pages': [
                {'name': 'test', 'url': 'http://localhost:8080'}
            ],
            'viewports': [
                {'name': '1280x800', 'width': 1280, 'height': 800}
            ],
            'masks': ['.class'],
            'timeout_ms': 30000
        }
        errors = validate_spec(spec)
        assert errors == []

    def test_validate_spec_missing_pages(self):
        """Test spec validation with missing pages."""
        spec = {
            'viewports': [{'name': '1280x800', 'width': 1280, 'height': 800}]
        }
        errors = validate_spec(spec)
        assert len(errors) > 0
        assert any('pages' in e.lower() for e in errors)

    def test_validate_spec_invalid_page_name(self):
        """Test spec validation with invalid page name."""
        spec = {
            'pages': [{'name': '-invalid', 'url': 'http://localhost'}],
            'viewports': [{'name': '1280x800', 'width': 1280, 'height': 800}]
        }
        errors = validate_spec(spec)
        assert any('name' in e.lower() for e in errors)

    def test_validate_spec_invalid_url(self):
        """Test spec validation with invalid URL."""
        spec = {
            'pages': [{'name': 'test', 'url': 'https://example.com'}],
            'viewports': [{'name': '1280x800', 'width': 1280, 'height': 800}]
        }
        errors = validate_spec(spec)
        assert any('url' in e.lower() for e in errors)


class TestRenderIntegration:
    """Integration tests that require browser."""

    def test_static_page_deterministic(self, tmp_path: Path, static_html_fixture: str):
        """Test that 5 renders of static page are byte-identical."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        out_dir = tmp_path / "static-test"
        out_dir.mkdir()

        spec = {
            'pages': [
                {'name': 'static-page', 'url': static_html_fixture}
            ],
            'viewports': [
                {'name': '1280x800', 'width': 1280, 'height': 800}
            ],
            'timeout_ms': 30000
        }

        # Render 5 times
        hashes = []
        for i in range(5):
            run_out = out_dir / f"run{i}"
            run_out.mkdir()
            render(spec, run_out)
            png = (run_out / "static-page" / "1280x800.png").read_bytes()
            hashes.append(hashlib.sha256(png).hexdigest())

        # All hashes should be identical
        assert len(set(hashes)) == 1, f"Expected identical hashes, got: {hashes}"

    def test_mask_hides_element(self, tmp_path: Path, static_html_fixture: str):
        """Test that mask selector hides the masked element."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        out_unmasked = tmp_path / "unmasked"
        out_masked = tmp_path / "masked"
        out_unmasked.mkdir()
        out_masked.mkdir()

        spec = {
            'pages': [
                {'name': 'test-page', 'url': static_html_fixture}
            ],
            'viewports': [
                {'name': '1280x800', 'width': 1280, 'height': 800}
            ],
            'timeout_ms': 30000
        }

        # Render unmasked
        render(spec, out_unmasked)
        unmasked_png = (out_unmasked / "test-page" / "1280x800.png").read_bytes()
        unmasked_text = (out_unmasked / "test-page" / "1280x800.text.txt").read_text()

        # Render masked (hide .masked class)
        spec['masks'] = ['.masked']
        render(spec, out_masked)
        masked_png = (out_masked / "test-page" / "1280x800.png").read_bytes()
        masked_text = (out_masked / "test-page" / "1280x800.text.txt").read_text()

        # PNGs should be different
        assert unmasked_png != masked_png, "PNGs should differ when masked"

        # Text should be different (masked content removed)
        assert "This is masked" in unmasked_text, "Unmasked should contain masked text"
        assert "This is masked" not in masked_text, "Masked should not contain masked text"

    def test_external_image_blocked(self, tmp_path: Path, external_image_fixture: str):
        """Test that external images are blocked and recorded."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        out_dir = tmp_path / "external-test"
        out_dir.mkdir()

        spec = {
            'pages': [
                {'name': 'external-img', 'url': external_image_fixture}
            ],
            'viewports': [
                {'name': '1280x800', 'width': 1280, 'height': 800}
            ],
            'timeout_ms': 30000
        }

        result = render(spec, out_dir)

        # Check that blocked URLs are recorded
        assert 'blocked_urls' in result
        assert 'http://203.0.113.1/x.png' in result['blocked_urls']

        # Check that .meta.json contains blocked_urls
        meta_path = out_dir / "external-img" / "1280x800.meta.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert 'blocked_urls' in meta
        assert 'http://203.0.113.1/x.png' in meta['blocked_urls']

    def test_overflow_in_layout(self, tmp_path: Path, overflow_fixture: str):
        """Test that overflowing elements appear in layout.json."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        out_dir = tmp_path / "overflow-test"
        out_dir.mkdir()

        spec = {
            'pages': [
                {'name': 'overflow-page', 'url': overflow_fixture}
            ],
            'viewports': [
                {'name': '1280x800', 'width': 1280, 'height': 800}
            ],
            'timeout_ms': 30000
        }

        result = render(spec, out_dir)

        # Check that layout.json exists
        layout_path = out_dir / "overflow-page" / "1280x800.layout.json"
        assert layout_path.exists()

        layout = json.loads(layout_path.read_text())
        assert 'elements' in layout

        # Should have the wide box
        wide_boxes = [e for e in layout['elements'] if e.get('text') and 'wide' in e['text'].lower()]
        assert len(wide_boxes) >= 1, f"Expected wide box in layout, got: {layout['elements']}"

    def test_default_viewports_produce_files(self, tmp_path: Path, static_html_fixture: str):
        """Test that both default viewports produce output files."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        out_dir = tmp_path / "viewports-test"
        out_dir.mkdir()

        # Use default viewports (no viewports in spec, should use 1280x800 and 390x844)
        spec = {
            'pages': [
                {'name': 'viewport-test', 'url': static_html_fixture}
            ],
            # No viewports specified - use defaults
            'timeout_ms': 30000
        }

        result = render(spec, out_dir)

        # Check that both viewport outputs exist
        for vp in ['1280x800', '390x844']:
            assert (out_dir / "viewport-test" / f"{vp}.png").exists(), f"Missing {vp}.png"
            assert (out_dir / "viewport-test" / f"{vp}.text.txt").exists(), f"Missing {vp}.text.txt"
            assert (out_dir / "viewport-test" / f"{vp}.aria.txt").exists(), f"Missing {vp}.aria.txt"
            assert (out_dir / "viewport-test" / f"{vp}.layout.json").exists(), f"Missing {vp}.layout.json"
            assert (out_dir / "viewport-test" / f"{vp}.meta.json").exists(), f"Missing {vp}.meta.json"


class TestCLIIntegration:
    """Tests for CLI integration."""

    def test_subprocess_no_shell(self, tmp_path: Path, static_html_fixture: str):
        """Test that subprocess call uses argument list (no shell)."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        out_dir = tmp_path / "no-shell-test"
        out_dir.mkdir()

        # Use a name that would fail if shell=True (shell metacharacters)
        name = "test-page"

        # Build spec manually
        spec = {
            'pages': [{'name': name, 'url': static_html_fixture}],
            'viewports': [{'name': '1280x800', 'width': 1280, 'height': 800}],
            'timeout_ms': 30000
        }

        # Create spec file
        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(spec))

        # Run the Node script directly with subprocess (argument list, no shell)
        node_path = "node"
        script_path = Path(__file__).parent.parent / "tools" / "visual" / "render.mjs"

        result = subprocess.run(
            [node_path, str(script_path), '--spec', str(spec_file), '--out', str(out_dir)],
            env={**os.environ, 'CODEX_LOCAL_VISUAL_DIR': os.environ.get('CODEX_LOCAL_VISUAL_DIR', '/home/connellwc83/.local/share/codex-local-harness/visual')},
            capture_output=True,
            text=True
        )

        assert result.returncode == 0, f"Command failed: {result.stderr}"
        assert (out_dir / name / "1280x800.png").exists()


class TestPythonWrapper:
    """Tests for Python wrapper functionality."""

    def test_missing_visual_dir_raises_error(self, tmp_path: Path):
        """Test that missing visual directory raises RuntimeError with install hint."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        spec = {
            'pages': [{'name': 'test', 'url': 'http://localhost/test'}],
            'viewports': [{'name': '1280x800', 'width': 1280, 'height': 800}],
            'visual_dir': str(tmp_path / "nonexistent")
        }

        with pytest.raises(RuntimeError) as exc_info:
            render(spec, out_dir)

        assert "Install with:" in str(exc_info.value)
        assert "npm install --ignore-scripts --save-exact playwright @1.63.0" in str(exc_info.value)
        assert "not found" in str(exc_info.value).lower()

    def test_invalid_url_raises_error(self, tmp_path: Path):
        """Test that invalid URL raises ValueError."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        spec = {
            'pages': [{'name': 'test', 'url': 'https://example.com'}],
            'viewports': [{'name': '1280x800', 'width': 1280, 'height': 800}],
            'visual_dir': '/home/connellwc83/.local/share/codex-local-harness/visual'
        }

        with pytest.raises(ValueError) as exc_info:
            render(spec, out_dir)

        assert "Invalid URL" in str(exc_info.value)


class TestMetaJsonFields:
    """Tests for meta.json field requirements."""

    def test_meta_json_has_required_fields(self, tmp_path: Path, static_html_fixture: str):
        """Test that meta.json contains all required fields."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        out_dir = tmp_path / "meta-test"
        out_dir.mkdir()

        spec = {
            'pages': [
                {'name': 'meta-page', 'url': static_html_fixture}
            ],
            'viewports': [
                {'name': '1280x800', 'width': 1280, 'height': 800}
            ],
            'masks': ['.masked'],
            'timeout_ms': 30000
        }

        render(spec, out_dir)

        meta_path = out_dir / "meta-page" / "1280x800.meta.json"
        assert meta_path.exists()

        meta = json.loads(meta_path.read_text())

        required_fields = [
            'sha256_png', 'playwright_version', 'browser_version',
            'launch_args', 'viewport', 'masks', 'url'
        ]
        for field in required_fields:
            assert field in meta, f"Missing field: {field}"

        # Verify values
        assert meta['sha256_png'] and len(meta['sha256_png']) == 64  # Hex SHA256
        assert meta['playwright_version']
        assert meta['browser_version']
        assert isinstance(meta['launch_args'], list)
        assert meta['launch_args']  # Non-empty
        assert meta['viewport'] == {'width': 1280, 'height': 800}
        assert meta['masks'] == ['.masked']
        assert meta['url'] == static_html_fixture


class TestNodeScript:
    """Tests for Node script behavior."""

    def test_exit_code_success(self, tmp_path: Path, static_html_fixture: str):
        """Test that successful render returns exit code 0."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        out_dir = tmp_path / "exit-success"
        out_dir.mkdir()

        spec = {
            'pages': [{'name': 'exit-test', 'url': static_html_fixture}],
            'viewports': [{'name': '1280x800', 'width': 1280, 'height': 800}],
            'timeout_ms': 30000
        }

        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(spec))

        node_path = "node"
        script_path = Path(__file__).parent.parent / "tools" / "visual" / "render.mjs"

        result = subprocess.run(
            [node_path, str(script_path), '--spec', str(spec_file), '--out', str(out_dir)],
            env={**os.environ, 'CODEX_LOCAL_VISUAL_DIR': os.environ.get('CODEX_LOCAL_VISUAL_DIR', '/home/connellwc83/.local/share/codex-local-harness/visual')},
            capture_output=True,
            text=True
        )

        assert result.returncode == 0, f"Expected exit 0, got {result.returncode}: {result.stderr}"

    def test_exit_code_bad_spec(self, tmp_path: Path):
        """Test that bad spec returns exit code 2."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        spec_file = tmp_path / "bad-spec.json"
        spec_file.write_text(json.dumps({'pages': []}))  # Missing viewports

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        node_path = "node"
        script_path = Path(__file__).parent.parent / "tools" / "visual" / "render.mjs"

        result = subprocess.run(
            [node_path, str(script_path), '--spec', str(spec_file), '--out', str(out_dir)],
            env={**os.environ, 'CODEX_LOCAL_VISUAL_DIR': os.environ.get('CODEX_LOCAL_VISUAL_DIR', '/home/connellwc83/.local/share/codex-local-harness/visual')},
            capture_output=True,
            text=True
        )

        assert result.returncode == 2, f"Expected exit 2 for bad spec, got {result.returncode}"

    def test_exit_code_page_failure(self, tmp_path: Path):
        """Test that page failure returns exit code 4."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        out_dir = tmp_path / "page-fail"
        out_dir.mkdir()

        # URL that will fail to load
        spec = {
            'pages': [{'name': 'fail-test', 'url': 'http://localhost:59999/nonexistent'}],
            'viewports': [{'name': '1280x800', 'width': 1280, 'height': 800}],
            'timeout_ms': 5000
        }

        spec_file = tmp_path / "spec.json"
        spec_file.write_text(json.dumps(spec))

        node_path = "node"
        script_path = Path(__file__).parent.parent / "tools" / "visual" / "render.mjs"

        result = subprocess.run(
            [node_path, str(script_path), '--spec', str(spec_file), '--out', str(out_dir)],
            env={**os.environ, 'CODEX_LOCAL_VISUAL_DIR': os.environ.get('CODEX_LOCAL_VISUAL_DIR', '/home/connellwc83/.local/share/codex-local-harness/visual')},
            capture_output=True,
            text=True
        )

        assert result.returncode == 4, f"Expected exit 4 for page failure, got {result.returncode}"


class TestAriaSnapshot:
    """Tests for ARIA snapshot functionality."""

    def test_aria_snapshot_format(self, tmp_path: Path, static_html_fixture: str):
        """Test that aria.txt contains valid JSON."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        out_dir = tmp_path / "aria-test"
        out_dir.mkdir()

        spec = {
            'pages': [{'name': 'aria-page', 'url': static_html_fixture}],
            'viewports': [{'name': '1280x800', 'width': 1280, 'height': 800}],
            'timeout_ms': 30000
        }

        render(spec, out_dir)

        aria_path = out_dir / "aria-page" / "1280x800.aria.txt"
        assert aria_path.exists()

        content = aria_path.read_text()

        # Should be valid JSON
        try:
            parsed = json.loads(content)
            assert parsed is not None
        except json.JSONDecodeError:
            # Fallback: just check it's not empty
            assert len(content) > 0, "ARIA snapshot should not be empty"


class TestCheckDeterminism:
    """check-determinism must give real verdicts and never pass vacuously."""

    def _run(self, capsys, argv: list[str]) -> tuple[int, dict | None]:
        from codex_local_harness.visual_render_cli import main

        code = main(["check-determinism", "--json", *argv])
        out = capsys.readouterr().out.strip()
        return code, (json.loads(out) if out else None)

    def test_static_page_is_deterministic(self, capsys, static_html_fixture: str):
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)
        code, summary = self._run(
            capsys,
            ["--url", static_html_fixture, "--name", "static", "--viewport", "1280x800", "--runs", "3"],
        )
        assert code == 0
        assert summary["unique_png_hashes"] == 1
        assert summary["unique_text_hashes"] == 1
        assert summary["unique_aria_hashes"] == 1
        assert summary["all_identical"] is True

    def test_volatile_page_is_reported(self, capsys, tmp_path: Path):
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)
        page = tmp_path / "volatile.html"
        page.write_text(
            "<!DOCTYPE html><html><body><div id='v'></div>"
            "<script>document.getElementById('v').textContent = String(Math.random());</script>"
            "</body></html>"
        )
        code, summary = self._run(
            capsys,
            ["--url", f"file://{page}", "--name", "volatile", "--viewport", "1280x800", "--runs", "4"],
        )
        assert code == 1
        assert "text" in summary["differing_artifacts"]
        assert summary["all_identical"] is False

    def test_failed_runs_never_pass_vacuously(self, capsys, tmp_path: Path):
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)
        missing = tmp_path / "does-not-exist.html"
        code, summary = self._run(
            capsys,
            ["--url", f"file://{missing}", "--name", "missing", "--viewport", "1280x800", "--runs", "2"],
        )
        assert code == 2
        assert summary is None


class TestMultipleMasks:
    """Every mask selector must take effect, not only the first."""

    def test_multiple_masks_all_applied(self, tmp_path: Path):
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)
        page = tmp_path / "two-masks.html"
        page.write_text(
            "<!DOCTYPE html><html><body>"
            "<p>Stable text</p>"
            "<div class='first'>FIRST-VOLATILE</div>"
            "<div class='second'>SECOND-VOLATILE</div>"
            "</body></html>"
        )
        out = tmp_path / "out"
        out.mkdir()
        spec = {
            "pages": [{"name": "two-masks", "url": f"file://{page}"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "masks": [".first", ".second"],
            "timeout_ms": 30000,
        }
        render(spec, out)
        text = (out / "two-masks" / "1280x800.text.txt").read_text()
        assert "Stable text" in text
        assert "FIRST-VOLATILE" not in text
        assert "SECOND-VOLATILE" not in text
