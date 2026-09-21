# Visual Render Tool

Deterministic screenshot capture for visual review using Playwright. This tool
enables reproducible screenshots taken on the Spark host (aarch64), never inside
the read-only worker containers.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                          Visual Render Components                            │
├──────────────────────────────────────────────────────────────────────────────┤
│  • render.mjs: Node ESM script that drives Playwright                        │
│  • visual_render.py: Python wrapper with spec validation                     │
│  • visual_render_cli.py: CLI with render and check-determinism commands      │
│  • Pinned Playwright 1.63.0 at ~/.local/share/codex-local-harness/visual/    │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Installation

### Install Playwright

The pinned Playwright 1.63.0 must be installed before rendering:

```bash
npm install --ignore-scripts --save-exact playwright@1.63.0
```

This installs Playwright without downloading browsers. The Chromium browser
comes from the system Playwright cache at `~/.cache/ms-playwright`. This
caches the browser binary once and reuses it across all renders.

### Set up CODEX_LOCAL_VISUAL_DIR

By default, the tool expects Playwright at:
`~/.local/share/codex-local-harness/visual`

Override with environment variable:
```bash
export CODEX_LOCAL_VISUAL_DIR=/custom/path/to/playwright
```

## Render Settings

The following settings ensure pixel-deterministic renders:

| Setting | Value | Reason |
|---------|-------|--------|
| `deviceScaleFactor` | 1 | Fixed DPI for consistent rendering |
| `locale` | 'en-US' | Consistent text layout and fonts |
| `timezoneId` | 'UTC' | No timezone-related content changes |
| `colorScheme` | 'light' | Consistent theme handling |
| `reducedMotion` | 'reduce' | Disable animations |
| Font rendering | `--font-render-hinting=none` | Consistent font rendering |
| GPU | `--disable-gpu` | Avoid GPU-specific rendering differences |
| Color profile | `--force-color-profile=srgb` | Consistent color handling |
| Scrollbars | `--hide-scrollbars` | No scrollbar width variations |

### CSS Reset

All renders apply a global stylesheet that sets:
- `animation: none !important`
- `transition: none !important`
- `caret-color: transparent !important`

Pages are waited for `document.fonts.ready` and a 300ms settle period.

### Network Restrictions

Only these origins are allowed:
- The target page's own origin
- `file:`
- `data:`
- `blob:`

All other requests are aborted and recorded in `.meta.json`.

## CLI Usage

### Render Command

```bash
PYTHONPATH=src python3 -m codex_local_harness.visual_render_cli render \
  --url http://127.0.0.1:8765/dashboard \
  --name dashboard \
  --out ~/.local/share/codex-local-harness/renders \
  --viewport 1280x800 \
  --viewport 390x844 \
  --mask .spark-pulse \
  --mask .agent-brain --mask .freshness
```

Options:
- `--url`: URL to render (must start with `http://127.0.0.1`, `http://localhost`, or `file://`)
- `--name`: Name for output files
- `--out`: Output directory (created if needed)
- `--viewport`: Viewport size (WxH). Can be specified multiple times. Default: `1280x800`, `390x844`
- `--mask`: CSS selector to hide. Can be specified multiple times.
- `--json`: Output summary as JSON

Default output structure:
```
OUT/
└── NAME/
    ├── 1280x800.png          # Screenshot
    ├── 1280x800.text.txt     # document.body.innerText
    ├── 1280x800.aria.txt     # page.locator('body').ariaSnapshot() as JSON
    ├── 1280x800.layout.json  # Overflow elements
    └── 1280x800.meta.json    # Metadata (SHA256, versions, launch args, masks)
```

### Check Determinism Command

Render the same URL multiple times and verify outputs are byte-identical:

```bash
PYTHONPATH=src python3 -m codex_local_harness.visual_render_cli check-determinism \
  --url http://127.0.0.1:8765/dashboard \
  --name dashboard \
  --viewport 1280x800 \
  --mask .spark-pulse \
  --mask .agent-brain \
  --mask .freshness \
  --runs 10 \
  --json
```

Options:
- `--url`: URL to render
- `--name`: Name prefix for temporary renders
- `--viewport`: Single viewport size
- `--mask`: CSS selector to hide
- `--runs`: Number of render runs (default: 10)
- `--json`: Output summary as JSON

Exit codes:
- 0: All artifacts identical across runs
- 1: Some artifacts differ (reports which types in output)
- 2: Validation error
- 3: Runtime error

Output includes unique hash counts for PNG, text, and ARIA outputs, plus
which artifact types differ.

## Mutations

Mutations allow you to deterministically seed known defects into pages before
capturing screenshots. This is useful for testing how the rendering pipeline
handles various visual issues like wrong numbers, hidden elements, low contrast,
or overflow.

### Spec Field

Add a `mutations` field to your spec as an optional array (max 50 items) of
mutation objects:

```json
{
  "mutations": [
    {
      "id": "unique-id",
      "selector": "css-selector",
      "op": "set-text|set-style|set-attr|remove",
      "value": "mutation-specific-value",
      "index": 0
    }
  ]
}
```

### Mutation Fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique identifier matching `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` |
| `selector` | Yes | CSS selector (non-empty, no control chars) |
| `op` | Yes | Operation: `set-text`, `set-style`, `set-attr`, `remove` |
| `value` | Conditional | Required for `set-text`, `set-style`, `set-attr`; must be absent for `remove` |
| `index` | No | Zero-based index or `"all"` to change all matches; defaults to 0 |

### Operations

| Op | Value Format | Description |
|----|--------------|-------------|
| `set-text` | String (max 500 chars) | Sets `textContent` of matched element(s) |
| `set-style` | CSS declarations | Adds inline style with `!important` (no `{`, `}`, `<`, `>`, or `url(`) |
| `set-attr` | `name=value` | Sets attribute (allowed: `aria-label`, `title`, `alt`, `class`, or `data-*`) |
| `remove` | (no value) | Removes the element from the DOM |

### Behavior

- Mutations are applied after page load, after animation-off and mask stylesheets,
  but before the screenshot and all other outputs.
- Mutations are applied in list order.
- If a selector matches no elements, or `index` is out of range, rendering
  **fails** with exit code 4 and a JSON error on stderr naming the mutation id.
- Never skips silently; always fail loudly on errors.

### Example

Seed a wrong number, hide a loading indicator, and set aria-label:

```json
{
  "pages": [
    {
      "name": "dashboard",
      "url": "http://127.0.0.1:8765/"
    }
  ],
  "viewports": [
    {"name": "1280x800", "width": 1280, "height": 800}
  ],
  "mutations": [
    {
      "id": "wrong-number",
      "selector": ".stat-value",
      "op": "set-text",
      "value": "999,999",
      "index": 1
    },
    {
      "id": "hide-loader",
      "selector": ".loading-indicator",
      "op": "remove"
    },
    {
      "id": "aria-label",
      "selector": ".chart",
      "op": "set-attr",
      "value": "aria-label=Revenue Chart"
    }
  ],
  "timeout_ms": 30000
}
```

### Meta Output

The `.meta.json` file records the applied mutations with matched counts:

```json
{
  "sha256_png": "...",
  "playwright_version": "1.63.0",
  "browser_version": "chromium-...",
  "viewport": {"width": 1280, "height": 800},
  "masks": [".loader"],
  "mutations": [
    {
      "id": "wrong-number",
      "selector": ".stat-value",
      "op": "set-text",
      "index": 1,
      "matched": 2
    },
    {
      "id": "hide-loader",
      "selector": ".loading-indicator",
      "op": "remove",
      "matched": 1
    }
  ],
  "url": "http://127.0.0.1:8765/",
  "blocked_urls": []
}
```

Note: Values are not recorded in meta.json for security and determinism.

## Checks

The render tool can perform deterministic in-page accessibility and visual quality checks.
When enabled (default), it writes a `.checks.json` file for each page and viewport.

### Check Types

| Check ID | Severity | Description |
|----------|----------|-------------|
| `contrast` | warning | Text contrast below WCAG thresholds (4.5:1 for normal text, 3.0:1 for large text) |
| `clipped-text` | warning | Text overflow hidden or truncated with ellipsis |
| `page-overflow-x` | warning | Page width exceeds viewport width + 1px |
| `missing-accessible-name` | warning | Interactive elements without accessible names |
| `placeholder-text` | error | Text matching placeholder patterns (undefined, null, TODO, etc.) |
| `implausible-timestamp` | error | Epoch-zero timestamps or ≥20 years ago |
| `tiny-text` | info | Font size below 10px |
| `repeated-unavailable` | info | "Unavailable" appears ≥3 times in an element |

### Output Schema

```json
{
  "version": 1,
  "stats": {
    "elements_examined": 150,
    "page_height": 2000,
    "viewport_height": 800,
    "page_height_ratio": 2.5,
    "unavailable_count": 5,
    "contrast_unknown": 2
  },
  "summary": {
    "contrast": 3,
    "clipped-text": 1,
    "page-overflow-x": 0,
    "missing-accessible-name": 2,
    "placeholder-text": 1,
    "implausible-timestamp": 0,
    "tiny-text": 4,
    "repeated-unavailable": 1
  },
  "findings": [
    {
      "check": "contrast",
      "severity": "warning",
      "selector": "div.container > p.title:nth-of-type(1)",
      "box": { "x": 10, "y": 100, "width": 800, "height": 40 },
      "text": "Low contrast heading text...",
      "detail": "contrast ratio 3.20 (threshold: 4.5)"
    }
  ],
  "truncated": false
}
```

### Stats Fields

| Field | Description |
|-------|-------------|
| `elements_examined` | Count of visible elements analyzed |
| `page_height` | Document scrollHeight |
| `viewport_height` | Viewport height from spec |
| `page_height_ratio` | page_height / viewport_height (2 decimal places) |
| `unavailable_count` | Total occurrences of "Unavailable" in page text |
| `contrast_unknown` | Elements with non-computable backgrounds (images/gradients) |

### Findings Fields

| Field | Description |
|-------|-------------|
| `check` | Check ID |
| `severity` | `warning`, `error`, or `info` |
| `selector` | Short stable path (tag, id, classes, nth-of-type) |
| `box` | Bounding rectangle {x, y, width, height} |
| `text` | First 80 characters of text content (or null) |
| `detail` | Human-readable explanation |

### Checks Option

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `checks` | boolean | `true` | Enable or disable checks |

When `checks: false`, no `.checks.json` file is written. The `.meta.json` still
records `checks: false`.

```json
{
  "pages": [{ ... }],
  "viewports": [{ ... }],
  "checks": false
}
```

### Limits and Edge Cases

- Maximum 200 findings; additional findings are truncated with `truncated: true`
- Skips non-rendered elements (`display:none`, `visibility:hidden`, `opacity:0`, zero-size)
- Skips elements inside masked regions (mask selectors use `visibility:hidden`)
- Contrast unknown when background involves images or gradients
- Contrast unknown when color syntax is not `rgb(...)`/`rgba(...)`
- Summary keys are sorted alphabetically
- Findings are in document order

## Determinism Recipe for Live Pages

Live dashboards change between renders. To make them deterministic:

1. **Serve with replay mode**: Use `--mode replay` with the harness dashboard
   and the `qualification/v010` and `qualification/v011` replay files.

2. **Mask volatile regions**: Identify and hide elements that change between
   renders:
   - `.spark-pulse`: Animated status indicators
   - `.agent-brain`: Animated agent visualization
- `.freshness`: relative-time badge such as "38 minutes ago", which changes as the wall clock advances
   - Dynamic timestamps or counters

```bash
PYTHONPATH=src python3 -m codex_local_harness.visual_render_cli render \
  --url http://127.0.0.1:8765/ \
  --name harness-dashboard \
  --out ~/.local/share/codex-local-harness/renders \
  --mask .spark-pulse \
  --mask .agent-brain --mask .freshness
```

3. **Verify determinism**:
```bash
PYTHONPATH=src python3 -m codex_local_harness.visual_render_cli check-determinism \
  --url http://127.0.0.1:8765/ \
  --name harness-dashboard \
  --viewport 1280x800 \
  --mask .spark-pulse \
  --mask .agent-brain \
  --mask .freshness \
  --runs 10
```

Expected: 1 unique hash for each artifact type, exit code 0.

## Output Files

### PNG (`.png`)
Base64-encoded screenshot, fixed viewport (not full page).

### Text (`.text.txt`)
Plain text content of `document.body.innerText`. No formatting.

### ARIA (`.aria.txt`)
JSON snapshot from `page.locator('body').ariaSnapshot()`. Contains
accessibility tree structure.

### Layout (`.layout.json`)

```json
{
  "viewport": {"width": 1280, "height": 800},
  "elements": [
    {
      "tag": "div",
      "id": "main-content",
      "classes": ["container", "wide"],
      "box": {"x": 0, "y": 100, "width": 2000, "height": 100},
      "text": "Wide content that overflo..."
    }
  ]
}
```

Elements are included if:
- `scrollWidth > clientWidth + 1` or `scrollHeight > clientHeight + 1`
  with `overflow` not `visible`, OR
- Any bounding box extends beyond viewport width/height

Limited to 50 entries maximum.

### Meta (`.meta.json`)

```json
{
  "sha256_png": "abc123...",
  "playwright_version": "1.63.0",
  "browser_version": "chromium-123456",
  "launch_args": ["--font-render-hinting=none", "--disable-gpu", ...],
  "viewport": {"width": 1280, "height": 800},
  "masks": [".spark-pulse", ".agent-brain", ".freshness"],
  "url": "http://127.0.0.1:8765/",
  "blocked_urls": ["http://203.0.113.1/x.png"]
}
```

No timestamps included. This is deterministic metadata.

## Limitations

### Font Rendering

Pixel results are valid only for one Chromium build and font setup. Changes to:
- System fonts
- Chromium version
- Font rendering settings
- `--font-render-hinting` argument

...will produce different pixel outputs even for identical HTML.

### Live Content

Content that changes between renders (clocks, random numbers, dynamic data)
cannot be made deterministic through masking. The `check-determinism` command
helps identify such issues.

### Network Requests

External resources (images, fonts, APIs) are blocked by default. If your page
requires external content, consider:
- Serving all assets from the same origin
- Using `data:` URIs for critical resources
- Pre-caching in the replay files

## Testing

Run tests:

```bash
PYTHONPATH=src python3 -m pytest tests/test_visual_render.py -v
```

Tests include:
- Validation functions (always run)
- Static page determinism (requires Playwright)
- Mask functionality (requires Playwright)
- External URL blocking (requires Playwright)
- Overflow detection (requires Playwright)
- Both default viewports (requires Playwright)
- Exit codes (requires Playwright)
- ARIA snapshot format (requires Playwright)

## Integration with Dashboard

The visual render tool is designed to support:
- **Replay mode testing**: Render dashboard pages from recorded sessions
- **Regression detection**: Compare new renders against baselines
- **PR reviews**: Include screenshots in review workflows
- **Documentation**: Generate consistent UI screenshots

The harness dashboard should be served in replay mode during visual reviews:
```bash
codex-harness-dashboard --mode replay --replay qualification/v010 --replay qualification/v011
```

Then mask volatile elements and render as described in the determinism recipe.

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Uncaught error |
| 2 | Bad spec file (invalid JSON, missing fields, validation errors) |
| 3 | Playwright or browser cannot be launched |
| 4 | Page failure (load timeout, navigation error) |

The Node script outputs JSON to stdout with summary information. The Python
wrapper interprets these codes and raises appropriate exceptions.

## Troubleshooting

### "Playwright not found"

```bash
export CODEX_LOCAL_VISUAL_DIR=/path/to/playwright
npm install --ignore-scripts --save-exact playwright@1.63.0
```

### "Browser cannot be launched"

The Chromium browser is installed by Playwright during initial setup. Install it:

```bash
npx playwright install chromium
```

Or ensure `~/.cache/ms-playwright` contains the Chromium binary.

### "Exit code 4 (page failure)"

The page failed to load within the timeout. Increase `--timeout` or check:
- URL is accessible from the Spark host
- Server is running and responding
- Network restrictions aren't blocking required resources

### "Some artifacts differ"

Run `check-determinism` with `--json` to see which artifact types differ:
- PNG differs: Layout or rendering issues
- Text differs: Content changes between renders
- ARIA differs: Accessibility tree changes

Mask volatile elements or use replay mode to freeze content.

## Known limitation for live dashboards

With the three masks above, the harness dashboard in replay mode gave 12 identical PNG, text and ARIA hashes at both viewports. The layout report covers the whole document, not only the visible viewport, and at 1280x800 it occasionally differed (page height 3665 versus 3684) because another unmasked live section lower on the page changes height. `check-determinism` reports this as a `layout` difference. Add masks for further volatile sections when a baseline needs the full-page layout to be stable; viewport screenshots, text and ARIA output were already stable.
