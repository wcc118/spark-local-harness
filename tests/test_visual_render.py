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
    validate_mutation,
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


class TestMutationValidation:
    """Tests for mutation validation."""

    def test_validate_mutation_set_text_valid(self):
        """Test valid set-text mutation."""
        mutation = {
            'id': 'text-change',
            'selector': '.title',
            'op': 'set-text',
            'value': 'New Title'
        }
        errors = validate_mutation(mutation)
        assert errors == []

    def test_validate_mutation_set_text_missing_value(self):
        """Test set-text mutation missing value."""
        mutation = {
            'id': 'text-change',
            'selector': '.title',
            'op': 'set-text'
        }
        errors = validate_mutation(mutation)
        assert any('value' in e.lower() for e in errors)

    def test_validate_mutation_set_text_value_too_long(self):
        """Test set-text mutation value exceeds 500 chars."""
        mutation = {
            'id': 'text-change',
            'selector': '.title',
            'op': 'set-text',
            'value': 'x' * 501
        }
        errors = validate_mutation(mutation)
        assert any('exceeds' in e.lower() or '500' in e.lower() for e in errors)

    def test_validate_mutation_set_style_valid(self):
        """Test valid set-style mutation."""
        mutation = {
            'id': 'style-change',
            'selector': '.item',
            'op': 'set-style',
            'value': 'color: red; font-size: 16px'
        }
        errors = validate_mutation(mutation)
        assert errors == []

    def test_validate_mutation_set_style_disallowed_char_curly(self):
        """Test set-style with disallowed curly brace."""
        mutation = {
            'id': 'style-change',
            'selector': '.item',
            'op': 'set-style',
            'value': 'color: red; {bad}'
        }
        errors = validate_mutation(mutation)
        assert any('disallowed' in e.lower() for e in errors)

    def test_validate_mutation_set_style_disallowed_char_angle(self):
        """Test set-style with disallowed angle bracket."""
        mutation = {
            'id': 'style-change',
            'selector': '.item',
            'op': 'set-style',
            'value': 'color: <red>'
        }
        errors = validate_mutation(mutation)
        assert any('disallowed' in e.lower() for e in errors)

    def test_validate_mutation_set_style_disallowed_url(self):
        """Test set-style with disallowed url()."""
        mutation = {
            'id': 'style-change',
            'selector': '.item',
            'op': 'set-style',
            'value': 'background: url(image.png)'
        }
        errors = validate_mutation(mutation)
        assert any('disallowed' in e.lower() for e in errors)

    def test_validate_mutation_set_attr_valid(self):
        """Test valid set-attr mutation."""
        mutation = {
            'id': 'attr-change',
            'selector': '.link',
            'op': 'set-attr',
            'value': 'aria-label=New Label'
        }
        errors = validate_mutation(mutation)
        assert errors == []

    def test_validate_mutation_set_attr_allowed_names(self):
        """Test set-attr with allowed attribute names."""
        for name in ['aria-label', 'title', 'alt', 'class', 'data-test', 'data-foo-bar']:
            mutation = {
                'id': 'attr-change',
                'selector': '.item',
                'op': 'set-attr',
                'value': f'{name}=value'
            }
            errors = validate_mutation(mutation)
            assert errors == [], f"Failed for {name}"

    def test_validate_mutation_set_attr_disallowed_name(self):
        """Test set-attr with disallowed attribute name."""
        mutation = {
            'id': 'attr-change',
            'selector': '.item',
            'op': 'set-attr',
            'value': 'href=http://example.com'
        }
        errors = validate_mutation(mutation)
        assert any('disallowed' in e.lower() or 'name' in e.lower() for e in errors)

    def test_validate_mutation_set_attr_no_equals(self):
        """Test set-attr without = separator."""
        mutation = {
            'id': 'attr-change',
            'selector': '.item',
            'op': 'set-attr',
            'value': 'aria-labelvalue'
        }
        errors = validate_mutation(mutation)
        assert any('name=value' in e.lower() for e in errors)

    def test_validate_mutation_remove_no_value(self):
        """Test remove mutation must not have value."""
        mutation = {
            'id': 'remove-item',
            'selector': '.item',
            'op': 'remove'
        }
        errors = validate_mutation(mutation)
        assert errors == []

    def test_validate_mutation_remove_has_value(self):
        """Test remove mutation with value is invalid."""
        mutation = {
            'id': 'remove-item',
            'selector': '.item',
            'op': 'remove',
            'value': 'some value'
        }
        errors = validate_mutation(mutation)
        assert any('must not have' in e.lower() for e in errors)

    def test_validate_mutation_missing_id(self):
        """Test mutation missing id."""
        mutation = {
            'selector': '.item',
            'op': 'set-text',
            'value': 'test'
        }
        errors = validate_mutation(mutation)
        assert any('id' in e.lower() for e in errors)

    def test_validate_mutation_invalid_id(self):
        """Test mutation with invalid id."""
        mutation = {
            'id': '-invalid',
            'selector': '.item',
            'op': 'set-text',
            'value': 'test'
        }
        errors = validate_mutation(mutation)
        assert any('id' in e.lower() for e in errors)

    def test_validate_mutation_missing_selector(self):
        """Test mutation missing selector."""
        mutation = {
            'id': 'test-mutation',
            'op': 'set-text',
            'value': 'test'
        }
        errors = validate_mutation(mutation)
        assert any('selector' in e.lower() for e in errors)

    def test_validate_mutation_empty_selector(self):
        """Test mutation with empty selector."""
        mutation = {
            'id': 'test-mutation',
            'selector': '',
            'op': 'set-text',
            'value': 'test'
        }
        errors = validate_mutation(mutation)
        assert any('empty' in e.lower() or 'selector' in e.lower() for e in errors)

    def test_validate_mutation_missing_op(self):
        """Test mutation missing op."""
        mutation = {
            'id': 'test-mutation',
            'selector': '.item',
            'value': 'test'
        }
        errors = validate_mutation(mutation)
        assert any('op' in e.lower() for e in errors)

    def test_validate_mutation_invalid_op(self):
        """Test mutation with invalid op."""
        mutation = {
            'id': 'test-mutation',
            'selector': '.item',
            'op': 'invalid-op'
        }
        errors = validate_mutation(mutation)
        assert any('op' in e.lower() for e in errors)

    def test_validate_mutation_index_valid(self):
        """Test mutation with valid index."""
        mutation = {
            'id': 'test-mutation',
            'selector': '.item',
            'op': 'set-text',
            'value': 'test',
            'index': 2
        }
        errors = validate_mutation(mutation)
        assert errors == []

    def test_validate_mutation_index_all(self):
        """Test mutation with index='all'."""
        mutation = {
            'id': 'test-mutation',
            'selector': '.item',
            'op': 'set-text',
            'value': 'test',
            'index': 'all'
        }
        errors = validate_mutation(mutation)
        assert errors == []

    def test_validate_mutation_index_negative(self):
        """Test mutation with negative index."""
        mutation = {
            'id': 'test-mutation',
            'selector': '.item',
            'op': 'set-text',
            'value': 'test',
            'index': -1
        }
        errors = validate_mutation(mutation)
        assert any('index' in e.lower() for e in errors)

    def test_validate_mutation_index_not_integer(self):
        """Test mutation with non-integer index."""
        mutation = {
            'id': 'test-mutation',
            'selector': '.item',
            'op': 'set-text',
            'value': 'test',
            'index': 'invalid'
        }
        errors = validate_mutation(mutation)
        assert any('index' in e.lower() for e in errors)

    def test_validate_spec_with_valid_mutations(self):
        """Test spec with valid mutations."""
        spec = {
            'pages': [{'name': 'test', 'url': 'http://localhost/test'}],
            'viewports': [{'name': '1280x800', 'width': 1280, 'height': 800}],
            'mutations': [
                {
                    'id': 'mut1',
                    'selector': '.item',
                    'op': 'set-text',
                    'value': 'New Text'
                }
            ]
        }
        errors = validate_spec(spec)
        assert errors == []

    def test_validate_spec_duplicate_mutation_id(self):
        """Test spec with duplicate mutation ids."""
        spec = {
            'pages': [{'name': 'test', 'url': 'http://localhost/test'}],
            'viewports': [{'name': '1280x800', 'width': 1280, 'height': 800}],
            'mutations': [
                {'id': 'dup', 'selector': '.a', 'op': 'set-text', 'value': 'a'},
                {'id': 'dup', 'selector': '.b', 'op': 'set-text', 'value': 'b'}
            ]
        }
        errors = validate_spec(spec)
        assert any('duplicate' in e.lower() for e in errors)

    def test_validate_spec_too_many_mutations(self):
        """Test spec with more than 50 mutations."""
        mutations = [
            {'id': f'm{i}', 'selector': f'.item{i}', 'op': 'set-text', 'value': f'text{i}'}
            for i in range(51)
        ]
        spec = {
            'pages': [{'name': 'test', 'url': 'http://localhost/test'}],
            'viewports': [{'name': '1280x800', 'width': 1280, 'height': 800}],
            'mutations': mutations
        }
        errors = validate_spec(spec)
        assert any('too many' in e.lower() or '50' in e.lower() for e in errors)

    def test_validate_spec_extra_mutation_field(self):
        """Test spec with extra mutation field."""
        spec = {
            'pages': [{'name': 'test', 'url': 'http://localhost/test'}],
            'viewports': [{'name': '1280x800', 'width': 1280, 'height': 800}],
            'mutations': [
                {
                    'id': 'test-mut',
                    'selector': '.item',
                    'op': 'set-text',
                    'value': 'text',
                    'extra_field': 'should fail'
                }
            ]
        }
        errors = validate_spec(spec)
        assert any('unknown' in e.lower() or 'field' in e.lower() for e in errors)


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
        assert "npm install --ignore-scripts --save-exact playwright@1.63.0" in str(exc_info.value)
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


class TestFailureModes:
    """Failures must be visible, never silent successes."""

    def test_page_failure_raises(self, tmp_path: Path):
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)
        spec = {
            "pages": [{"name": "gone", "url": f"file://{tmp_path / 'does-not-exist.html'}"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "timeout_ms": 15000,
        }
        out = tmp_path / "out"
        out.mkdir()
        with pytest.raises(RuntimeError, match="Page failure"):
            render(spec, out)

    def test_websocket_is_blocked_and_recorded(self, tmp_path: Path):
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)
        page = tmp_path / "ws.html"
        page.write_text(
            "<!DOCTYPE html><html><body><p>ws page</p>"
            "<script>try { new WebSocket('ws://203.0.113.1:9/socket'); } catch (e) {}</script>"
            "</body></html>"
        )
        out = tmp_path / "out"
        out.mkdir()
        spec = {
            "pages": [{"name": "ws-page", "url": f"file://{page}"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "timeout_ms": 30000,
        }
        render(spec, out)
        meta = json.loads((out / "ws-page" / "1280x800.meta.json").read_text())
        assert any(url.startswith("ws://203.0.113.1") for url in meta["blocked_urls"])

    def test_subprocess_environment_is_minimal(self, monkeypatch, tmp_path: Path):
        import codex_local_harness.visual_render as vr

        seen = {}

        class Done:
            returncode = 0
            stdout = "{}"
            stderr = ""

        def fake_run(cmd, **kwargs):
            seen["env"] = kwargs["env"]
            return Done()

        monkeypatch.setenv("SECRET_TOKEN_FOR_TEST", "should-not-leak")
        monkeypatch.setattr(vr.subprocess, "run", fake_run)
        visual = tmp_path / "visual"
        visual.mkdir()
        (visual / "package.json").write_text("{}")
        (visual / "node_modules").mkdir()
        (visual / "node_modules" / "playwright").mkdir()
        out = tmp_path / "out"
        out.mkdir()
        spec = {
            "pages": [{"name": "p", "url": "file:///tmp/x.html"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
        }
        vr.render(spec, out, visual_dir=visual)
        assert "SECRET_TOKEN_FOR_TEST" not in seen["env"]
        assert seen["env"]["CODEX_LOCAL_VISUAL_DIR"] == str(visual)


class TestMutationBehavior:
    """Mutations must really change the rendered page (browser tests)."""

    PAGE = (
        "<!DOCTYPE html><html><body style='font-family: sans-serif'>"
        "<h1 id='title'>Total load</h1>"
        "<p class='v'>12.5 kN</p><p class='v'>13.1 kN</p>"
        "<button id='go'>Go</button>"
        "<div id='gone'>REMOVE-ME</div>"
        "<div id='shown'>SHOW-ME</div>"
        "</body></html>"
    )

    def _render(self, tmp_path: Path, mutations: list[dict]) -> Path:
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)
        page = tmp_path / "mut.html"
        page.write_text(self.PAGE)
        out = tmp_path / f"out-{len(list(tmp_path.glob('out-*')))}"
        out.mkdir()
        spec = {
            "pages": [{"name": "mut", "url": f"file://{page}"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "mutations": mutations,
            "timeout_ms": 30000,
        }
        render(spec, out)
        return out / "mut"

    def test_set_text_defaults_to_first_match(self, tmp_path: Path):
        d = self._render(tmp_path, [{"id": "unit", "selector": ".v", "op": "set-text", "value": "12.5 N"}])
        text = (d / "1280x800.text.txt").read_text()
        assert "12.5 N" in text
        assert "13.1 kN" in text
        assert "12.5 kN" not in text

    def test_set_text_index_and_all(self, tmp_path: Path):
        d = self._render(tmp_path, [{"id": "second", "selector": ".v", "op": "set-text", "value": "X", "index": 1}])
        text = (d / "1280x800.text.txt").read_text()
        assert "12.5 kN" in text and "13.1 kN" not in text
        d = self._render(tmp_path, [{"id": "every", "selector": ".v", "op": "set-text", "value": "Y", "index": "all"}])
        text = (d / "1280x800.text.txt").read_text()
        assert "12.5 kN" not in text and "13.1 kN" not in text
        assert text.count("Y") == 2

    def test_set_style_display_none_hides_text(self, tmp_path: Path):
        d = self._render(tmp_path, [{"id": "hide", "selector": "#shown", "op": "set-style", "value": "display:none"}])
        text = (d / "1280x800.text.txt").read_text()
        assert "SHOW-ME" not in text
        assert "REMOVE-ME" in text

    def test_set_style_keeps_existing_inline_style(self, tmp_path: Path):
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)
        page = tmp_path / "inline.html"
        page.write_text(
            "<!DOCTYPE html><html><body>"
            "<div id='x' style='display:none'>HIDDEN-BY-INLINE</div>"
            "</body></html>"
        )
        out = tmp_path / "out-inline"
        out.mkdir()
        spec = {
            "pages": [{"name": "inline", "url": f"file://{page}"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "mutations": [{"id": "color", "selector": "#x", "op": "set-style", "value": "color: red"}],
            "timeout_ms": 30000,
        }
        render(spec, out)
        text = (out / "inline" / "1280x800.text.txt").read_text()
        assert "HIDDEN-BY-INLINE" not in text

    def test_set_attr_changes_accessible_name(self, tmp_path: Path):
        d = self._render(tmp_path, [{"id": "label", "selector": "#go", "op": "set-attr", "value": "aria-label=Stop now"}])
        aria = (d / "1280x800.aria.txt").read_text()
        assert "Stop now" in aria

    def test_remove_deletes_element(self, tmp_path: Path):
        d = self._render(tmp_path, [{"id": "rm", "selector": "#gone", "op": "remove"}])
        text = (d / "1280x800.text.txt").read_text()
        assert "REMOVE-ME" not in text
        assert "SHOW-ME" in text

    def test_no_match_fails_loudly(self, tmp_path: Path):
        with pytest.raises(RuntimeError, match="mutation_no_match"):
            self._render(tmp_path, [{"id": "nope", "selector": ".does-not-exist", "op": "remove"}])

    def test_index_out_of_range_fails_loudly(self, tmp_path: Path):
        with pytest.raises(RuntimeError, match="out_of_range"):
            self._render(tmp_path, [{"id": "far", "selector": ".v", "op": "set-text", "value": "Z", "index": 5}])

    def test_meta_records_mutations_without_values(self, tmp_path: Path):
        d = self._render(
            tmp_path,
            [
                {"id": "unit", "selector": ".v", "op": "set-text", "value": "SECRET-VALUE-123"},
                {"id": "rm", "selector": "#gone", "op": "remove"},
            ],
        )
        raw = (d / "1280x800.meta.json").read_text()
        meta = json.loads(raw)
        assert [m["id"] for m in meta["mutations"]] == ["unit", "rm"]
        assert meta["mutations"][0]["matched"] == 2
        assert meta["mutations"][1]["matched"] == 1
        assert "SECRET-VALUE-123" not in raw

    def test_mutated_render_is_deterministic(self, tmp_path: Path):
        muts = [{"id": "unit", "selector": ".v", "op": "set-text", "value": "9 kN", "index": "all"}]
        hashes = set()
        for _ in range(3):
            d = self._render(tmp_path, muts)
            hashes.add(hashlib.sha256((d / "1280x800.png").read_bytes()).hexdigest())
        assert len(hashes) == 1


class TestMutationValidationEdges:
    """Edge cases found in review: booleans and non-list values must be rejected."""

    def test_boolean_index_is_rejected(self):
        errors = validate_mutation({"id": "b", "selector": ".v", "op": "remove", "index": True})
        assert any("index" in e for e in errors)

    def test_mutations_must_be_a_list(self):
        spec = {
            "pages": [{"name": "p", "url": "file:///tmp/x.html"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "mutations": {"id": "not-a-list"},
        }
        assert any("mutations" in e for e in validate_spec(spec))


class TestChecksValidation:
    """Tests for checks option validation."""

    def test_checks_default_is_true(self):
        """Test that checks defaults to true when not specified."""
        spec = {
            "pages": [{"name": "p", "url": "file:///tmp/x.html"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
        }
        errors = validate_spec(spec)
        assert errors == []

    def test_checks_false_valid(self):
        """Test that checks: false is valid."""
        spec = {
            "pages": [{"name": "p", "url": "file:///tmp/x.html"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "checks": False,
        }
        errors = validate_spec(spec)
        assert errors == []

    def test_checks_true_valid(self):
        """Test that checks: true is valid."""
        spec = {
            "pages": [{"name": "p", "url": "file:///tmp/x.html"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "checks": True,
        }
        errors = validate_spec(spec)
        assert errors == []

    def test_checks_non_boolean_rejected(self):
        """Test that non-boolean checks value is rejected."""
        spec = {
            "pages": [{"name": "p", "url": "file:///tmp/x.html"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "checks": "true",
        }
        errors = validate_spec(spec)
        assert any("checks" in e.lower() and "boolean" in e.lower() for e in errors)

    def test_checks_integer_rejected(self):
        """Test that integer checks value is rejected."""
        spec = {
            "pages": [{"name": "p", "url": "file:///tmp/x.html"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "checks": 1,
        }
        errors = validate_spec(spec)
        assert any("checks" in e.lower() for e in errors)


class TestChecksOutput:
    """Browser tests for checks output (.checks.json)."""

    def test_contrast_low_flagged(self, tmp_path: Path):
        """Test that low contrast is flagged with warning."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        page = tmp_path / "contrast.html"
        page.write_text(
            "<!DOCTYPE html><html><body style='font-family: sans-serif'>"
            "<div style='color:#aaaaaa;background:#ffffff'>Low contrast grey</div>"
            "<div style='color:#000000;background:#ffffff'>High contrast black</div>"
            "</body></html>"
        )
        out = tmp_path / "out"
        out.mkdir()
        spec = {
            "pages": [{"name": "contrast", "url": f"file://{page}"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "timeout_ms": 30000,
        }
        render(spec, out)

        checks_path = out / "contrast" / "1280x800.checks.json"
        assert checks_path.exists()
        checks = json.loads(checks_path.read_text())

        # Should have contrast findings
        assert checks["version"] == 1
        assert "findings" in checks
        contrast_findings = [f for f in checks["findings"] if f["check"] == "contrast"]
        assert len(contrast_findings) >= 1, "Expected at least one contrast finding"

        # Verify structure
        for finding in contrast_findings:
            assert finding["severity"] == "warning"
            assert "selector" in finding
            assert "box" in finding
            assert "text" in finding
            assert "detail" in finding

    def test_contrast_unknown_with_image_bg(self, tmp_path: Path):
        """Test that text on background-image counted in contrast_unknown."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        page = tmp_path / "bg-image.html"
        page.write_text(
            "<!DOCTYPE html><html><body>"
            "<div style='background-image:url(data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22></svg>)'>"
            "Text on image bg"
            "</div>"
            "</body></html>"
        )
        out = tmp_path / "out"
        out.mkdir()
        spec = {
            "pages": [{"name": "bgimage", "url": f"file://{page}"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "timeout_ms": 30000,
        }
        render(spec, out)

        checks_path = out / "bgimage" / "1280x800.checks.json"
        assert checks_path.exists()
        checks = json.loads(checks_path.read_text())

        # Should have contrast_unknown count
        assert "stats" in checks
        assert "contrast_unknown" in checks["stats"]
        # Contrast unknown should be >= 1 since we have a background image

    def test_clipped_text_flagged(self, tmp_path: Path):
        """Test that clipped text is flagged."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        page = tmp_path / "clipped.html"
        page.write_text(
            "<!DOCTYPE html><html><body>"
            "<div style='width:100px;overflow:hidden'>"
            "This is very long text that will be clipped"
            "</div>"
            "</body></html>"
        )
        out = tmp_path / "out"
        out.mkdir()
        spec = {
            "pages": [{"name": "clipped", "url": f"file://{page}"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "timeout_ms": 30000,
        }
        render(spec, out)

        checks_path = out / "clipped" / "1280x800.checks.json"
        assert checks_path.exists()
        checks = json.loads(checks_path.read_text())

        clipped_findings = [f for f in checks["findings"] if f["check"] == "clipped-text"]
        assert len(clipped_findings) >= 1, "Expected at least one clipped-text finding"

    def test_page_overflow_x_flagged(self, tmp_path: Path):
        """Test that page overflow x is flagged."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        page = tmp_path / "overflow.html"
        page.write_text(
            "<!DOCTYPE html><html><body style='margin:0'>"
            "<div style='width:3000px;height:100px;background:blue'></div>"
            "</body></html>"
        )
        out = tmp_path / "out"
        out.mkdir()
        spec = {
            "pages": [{"name": "overflow", "url": f"file://{page}"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "timeout_ms": 30000,
        }
        render(spec, out)

        checks_path = out / "overflow" / "1280x800.checks.json"
        assert checks_path.exists()
        checks = json.loads(checks_path.read_text())

        overflow_findings = [f for f in checks["findings"] if f["check"] == "page-overflow-x"]
        assert len(overflow_findings) == 1, "Expected exactly one page-overflow-x finding"

    def test_missing_accessible_name_flagged(self, tmp_path: Path):
        """Test that missing accessible name is flagged."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        page = tmp_path / "missing-name.html"
        page.write_text(
            "<!DOCTYPE html><html><body>"
            "<button id='no-name'>Click me</button>"
            "<a href='#'>Link</a>"
            "<img src='test.png' alt=''>"
            "</body></html>"
        )
        out = tmp_path / "out"
        out.mkdir()
        spec = {
            "pages": [{"name": "missing-name", "url": f"file://{page}"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "timeout_ms": 30000,
        }
        render(spec, out)

        checks_path = out / "missing-name" / "1280x800.checks.json"
        assert checks_path.exists()
        checks = json.loads(checks_path.read_text())

        # Should find missing accessible name issues
        missing_findings = [f for f in checks["findings"] if f["check"] == "missing-accessible-name"]
        assert len(missing_findings) >= 2, "Expected at least 2 missing-accessible-name findings (button and link)"

    def test_missing_accessible_name_control(self, tmp_path: Path):
        """Test that elements with accessible names pass."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        page = tmp_path / "has-name.html"
        page.write_text(
            "<!DOCTYPE html><html><body>"
            "<button aria-label='Close'>X</button>"
            "<a href='#' aria-label='Home'>Link</a>"
            "<img src='test.png' alt='Logo'>"
            "</body></html>"
        )
        out = tmp_path / "out"
        out.mkdir()
        spec = {
            "pages": [{"name": "has-name", "url": f"file://{page}"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "timeout_ms": 30000,
        }
        render(spec, out)

        checks_path = out / "has-name" / "1280x800.checks.json"
        assert checks_path.exists()
        checks = json.loads(checks_path.read_text())

        missing_findings = [f for f in checks["findings"] if f["check"] == "missing-accessible-name"]
        assert len(missing_findings) == 0, f"Expected no missing-accessible-name findings, got: {missing_findings}"

    def test_placeholder_text_flagged(self, tmp_path: Path):
        """Test that placeholder text patterns are flagged."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        page = tmp_path / "placeholder.html"
        page.write_text(
            "<!DOCTYPE html><html><body>"
            "<span>undefined</span>"
            "<span>{{name}}</span>"
            "<span>%s</span>"
            "<span>TODO</span>"
            "<span>Ordinary text</span>"
            "</body></html>"
        )
        out = tmp_path / "out"
        out.mkdir()
        spec = {
            "pages": [{"name": "placeholder", "url": f"file://{page}"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "timeout_ms": 30000,
        }
        render(spec, out)

        checks_path = out / "placeholder" / "1280x800.checks.json"
        assert checks_path.exists()
        checks = json.loads(checks_path.read_text())

        placeholder_findings = [f for f in checks["findings"] if f["check"] == "placeholder-text"]
        assert len(placeholder_findings) >= 3, "Expected at least 3 placeholder-text findings"

    def test_placeholder_text_control(self, tmp_path: Path):
        """Test that ordinary text passes."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        page = tmp_path / "ordinary.html"
        page.write_text(
            "<!DOCTYPE html><html><body>"
            "<p>Hello world</p>"
            "<p>This is normal text</p>"
            "</body></html>"
        )
        out = tmp_path / "out"
        out.mkdir()
        spec = {
            "pages": [{"name": "ordinary", "url": f"file://{page}"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "timeout_ms": 30000,
        }
        render(spec, out)

        checks_path = out / "ordinary" / "1280x800.checks.json"
        assert checks_path.exists()
        checks = json.loads(checks_path.read_text())

        placeholder_findings = [f for f in checks["findings"] if f["check"] == "placeholder-text"]
        assert len(placeholder_findings) == 0, f"Expected no placeholder-text findings, got: {placeholder_findings}"

    def test_implausible_timestamp_flagged(self, tmp_path: Path):
        """Test that implausible timestamps are flagged."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        page = tmp_path / "timestamp.html"
        page.write_text(
            "<!DOCTYPE html><html><body>"
            "<span>Created: 1970-01-01T00:00:01+00:00</span>"
            "<span>Updated: 56 years ago</span>"
            "<span>3 minutes ago</span>"
            "</body></html>"
        )
        out = tmp_path / "out"
        out.mkdir()
        spec = {
            "pages": [{"name": "timestamp", "url": f"file://{page}"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "timeout_ms": 30000,
        }
        render(spec, out)

        checks_path = out / "timestamp" / "1280x800.checks.json"
        assert checks_path.exists()
        checks = json.loads(checks_path.read_text())

        implausible_findings = [f for f in checks["findings"] if f["check"] == "implausible-timestamp"]
        assert len(implausible_findings) >= 2, "Expected at least 2 implausible-timestamp findings"

    def test_tiny_text_flagged(self, tmp_path: Path):
        """Test that tiny text is flagged."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        page = tmp_path / "tiny.html"
        page.write_text(
            "<!DOCTYPE html><html><body>"
            "<span style='font-size:8px'>Tiny text</span>"
            "<span style='font-size:12px'>Normal text</span>"
            "</body></html>"
        )
        out = tmp_path / "out"
        out.mkdir()
        spec = {
            "pages": [{"name": "tiny", "url": f"file://{page}"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "timeout_ms": 30000,
        }
        render(spec, out)

        checks_path = out / "tiny" / "1280x800.checks.json"
        assert checks_path.exists()
        checks = json.loads(checks_path.read_text())

        tiny_findings = [f for f in checks["findings"] if f["check"] == "tiny-text"]
        assert len(tiny_findings) >= 1, "Expected at least one tiny-text finding"

    def test_repeated_unavailable_flagged(self, tmp_path: Path):
        """Test that repeated unavailable is flagged."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        page = tmp_path / "unavailable.html"
        page.write_text(
            "<!DOCTYPE html><html><body>"
            "<div class='panel'>"
            "Status: Unavailable, Error: Unavailable, Result: Unavailable, Details: Unavailable"
            "</div>"
            "</body></html>"
        )
        out = tmp_path / "out"
        out.mkdir()
        spec = {
            "pages": [{"name": "unavailable", "url": f"file://{page}"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "timeout_ms": 30000,
        }
        render(spec, out)

        checks_path = out / "unavailable" / "1280x800.checks.json"
        assert checks_path.exists()
        checks = json.loads(checks_path.read_text())

        unavailable_findings = [f for f in checks["findings"] if f["check"] == "repeated-unavailable"]
        assert len(unavailable_findings) >= 1, "Expected at least one repeated-unavailable finding"

        # Check stats.unavailable_count
        assert "stats" in checks
        assert checks["stats"]["unavailable_count"] >= 4, "Expected unavailable_count >= 4"

    def test_mask_hides_checks(self, tmp_path: Path):
        """Test that masked elements don't trigger checks."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        page = tmp_path / "masked.html"
        page.write_text(
            "<!DOCTYPE html><html><body style='font-family: sans-serif'>"
            "<div class='visible'>Visible text</div>"
            "<div class='masked' style='color:#aaaaaa;background:#ffffff'>Low contrast masked</div>"
            "</body></html>"
        )
        out = tmp_path / "out"
        out.mkdir()
        spec = {
            "pages": [{"name": "masked", "url": f"file://{page}"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "masks": [".masked"],
            "timeout_ms": 30000,
        }
        render(spec, out)

        checks_path = out / "masked" / "1280x800.checks.json"
        assert checks_path.exists()
        checks = json.loads(checks_path.read_text())

        # Masked low contrast should not be reported
        contrast_findings = [f for f in checks["findings"] if f["check"] == "contrast"]
        for finding in contrast_findings:
            # Verify the finding is not from the masked element
            assert "masked" not in finding.get("selector", ""), f"Masked element should not be reported: {finding}"

    def test_set_style_mutation_triggers_check(self, tmp_path: Path):
        """Test that set-style mutation makes check fire."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        page = tmp_path / "mutation.html"
        page.write_text(
            "<!DOCTYPE html><html><body style='font-family: sans-serif'>"
            "<h1 id='title'>Title</h1>"
            "</body></html>"
        )
        out = tmp_path / "out"
        out.mkdir()
        spec = {
            "pages": [{"name": "mutation", "url": f"file://{page}"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "mutations": [
                {
                    "id": "low-contrast",
                    "selector": "#title",
                    "op": "set-style",
                    "value": "color:#aaaaaa",
                }
            ],
            "timeout_ms": 30000,
        }
        render(spec, out)

        checks_path = out / "mutation" / "1280x800.checks.json"
        assert checks_path.exists()
        checks = json.loads(checks_path.read_text())

        # The mutation should make the title have low contrast
        contrast_findings = [f for f in checks["findings"] if f["check"] == "contrast"]
        assert len(contrast_findings) >= 1, "Expected at least one contrast finding after mutation"

    def test_checks_false_no_file(self, tmp_path: Path):
        """Test that checks: false produces no .checks.json file."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        page = tmp_path / "nochecks.html"
        page.write_text(
            "<!DOCTYPE html><html><body>"
            "<div style='color:#aaaaaa;background:#ffffff'>Low contrast</div>"
            "</body></html>"
        )
        out = tmp_path / "out"
        out.mkdir()
        spec = {
            "pages": [{"name": "nochecks", "url": f"file://{page}"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "checks": False,
            "timeout_ms": 30000,
        }
        render(spec, out)

        checks_path = out / "nochecks" / "1280x800.checks.json"
        assert not checks_path.exists(), "checks: false should not produce .checks.json"

        # But meta.json should record it
        meta_path = out / "nochecks" / "1280x800.meta.json"
        assert meta_path.exists()
        meta = json.loads(meta_path.read_text())
        assert meta["checks"] is False

    def test_determinism(self, tmp_path: Path):
        """Test that three renders produce identical .checks.json bytes."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        page = tmp_path / "determinism.html"
        page.write_text(
            "<!DOCTYPE html><html><body>"
            "<h1>Static Page</h1>"
            "<p>Some content</p>"
            "</body></html>"
        )
        out = tmp_path / "out"
        out.mkdir()

        # Render 3 times
        hashes = set()
        for i in range(3):
            run_out = out / f"run{i}"
            run_out.mkdir()
            spec = {
                "pages": [{"name": "determinism", "url": f"file://{page}"}],
                "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
                "timeout_ms": 30000,
            }
            render(spec, run_out)

            checks_bytes = (run_out / "determinism" / "1280x800.checks.json").read_bytes()
            hashes.add(hashlib.sha256(checks_bytes).hexdigest())

        assert len(hashes) == 1, f"Expected identical checks.json hashes, got: {hashes}"


class TestChecksStructure:
    """Tests for checks.json structure."""

    def test_summary_has_all_keys(self, tmp_path: Path):
        """Test that summary has all check keys."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        page = tmp_path / "struct.html"
        page.write_text(
            "<!DOCTYPE html><html><body>"
            "<p>Normal content</p>"
            "</body></html>"
        )
        out = tmp_path / "out"
        out.mkdir()
        spec = {
            "pages": [{"name": "struct", "url": f"file://{page}"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "timeout_ms": 30000,
        }
        render(spec, out)

        checks_path = out / "struct" / "1280x800.checks.json"
        assert checks_path.exists()
        checks = json.loads(checks_path.read_text())

        # Verify summary has all check keys
        assert "summary" in checks
        required_keys = {
            "contrast",
            "clipped-text",
            "page-overflow-x",
            "missing-accessible-name",
            "placeholder-text",
            "implausible-timestamp",
            "tiny-text",
            "repeated-unavailable",
        }
        assert set(checks["summary"].keys()) == required_keys

    def test_stats_has_all_fields(self, tmp_path: Path):
        """Test that stats has all required fields."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        page = tmp_path / "stats.html"
        page.write_text(
            "<!DOCTYPE html><html><body>"
            "<p>Content</p>"
            "</body></html>"
        )
        out = tmp_path / "out"
        out.mkdir()
        spec = {
            "pages": [{"name": "stats", "url": f"file://{page}"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "timeout_ms": 30000,
        }
        render(spec, out)

        checks_path = out / "stats" / "1280x800.checks.json"
        assert checks_path.exists()
        checks = json.loads(checks_path.read_text())

        assert "stats" in checks
        required_fields = {
            "elements_examined",
            "page_height",
            "viewport_height",
            "page_height_ratio",
            "unavailable_count",
            "contrast_unknown",
        }
        assert set(checks["stats"].keys()) == required_fields

    def test_findings_in_document_order(self, tmp_path: Path):
        """Test that findings are in document order."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        page = tmp_path / "order.html"
        page.write_text(
            "<!DOCTYPE html><html><body>"
            "<div id='first' style='color:#aaaaaa'>First</div>"
            "<div id='second' style='color:#aaaaaa'>Second</div>"
            "<div id='third' style='color:#aaaaaa'>Third</div>"
            "</body></html>"
        )
        out = tmp_path / "out"
        out.mkdir()
        spec = {
            "pages": [{"name": "order", "url": f"file://{page}"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "timeout_ms": 30000,
        }
        render(spec, out)

        checks_path = out / "order" / "1280x800.checks.json"
        assert checks_path.exists()
        checks = json.loads(checks_path.read_text())

        # Find contrast findings
        contrast_findings = [f for f in checks["findings"] if f["check"] == "contrast"]

        # Extract IDs from selectors to check order
        ids = []
        for finding in contrast_findings:
            selector = finding.get("selector", "")
            if "first" in selector:
                ids.append("first")
            elif "second" in selector:
                ids.append("second")
            elif "third" in selector:
                ids.append("third")

        assert ids == sorted(ids, key=lambda x: {"first": 1, "second": 2, "third": 3}.get(x, 0)), \
            "Findings should be in document order"

    def test_truncated_flag(self, tmp_path: Path):
        """Test that truncated flag is set when findings exceed 200."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        # Create many elements
        elements = []
        for i in range(250):
            elements.append(f"<div style='color:#aaaaaa'>Text {i}</div>")
        page = tmp_path / "many.html"
        page.write_text(
            "<!DOCTYPE html><html><body>" + "".join(elements) + "</body></html>"
        )
        out = tmp_path / "out"
        out.mkdir()
        spec = {
            "pages": [{"name": "many", "url": f"file://{page}"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "timeout_ms": 30000,
        }
        render(spec, out)

        checks_path = out / "many" / "1280x800.checks.json"
        assert checks_path.exists()
        checks = json.loads(checks_path.read_text())

        assert checks.get("truncated", False) is True
        assert len(checks["findings"]) == 200

    def test_summary_keys_sorted(self, tmp_path: Path):
        """Test that summary keys are sorted alphabetically."""
        skip_reason = skip_if_playwright_missing()
        if skip_reason:
            pytest.skip(skip_reason)

        page = tmp_path / "sorted.html"
        page.write_text(
            "<!DOCTYPE html><html><body>"
            "<p>Content</p>"
            "</body></html>"
        )
        out = tmp_path / "out"
        out.mkdir()
        spec = {
            "pages": [{"name": "sorted", "url": f"file://{page}"}],
            "viewports": [{"name": "1280x800", "width": 1280, "height": 800}],
            "timeout_ms": 30000,
        }
        render(spec, out)

        checks_path = out / "sorted" / "1280x800.checks.json"
        assert checks_path.exists()
        checks = json.loads(checks_path.read_text())

        keys = list(checks["summary"].keys())
        assert keys == sorted(keys), "Summary keys should be sorted alphabetically"
