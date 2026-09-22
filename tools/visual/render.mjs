#!/usr/bin/env node
/**
 * Deterministic screenshot render tool using Playwright.
 *
 * Usage: node render.mjs --spec FILE --out DIR
 *
 * Exit codes:
 *   0 - Success
 *   2 - Bad spec file
 *   3 - Playwright or browser cannot be launched
 *   4 - Page failure
 */
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { resolve, dirname, basename, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { createHash } from 'node:crypto';

class MutationError extends Error {
  constructor(payload) {
    super(payload.message);
    this.payload = payload;
  }
}


const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Get Playwright directory from environment or default
const playwrightDir = process.env.CODEX_LOCAL_VISUAL_DIR ||
  join(homedir(), '.local', 'share', 'codex-local-harness', 'visual');
const requireFromPinnedDir = createRequire(resolve(playwrightDir, 'package.json'));

// Resolve Playwright from the pinned directory
let playwrightPackage;
try {
  playwrightPackage = requireFromPinnedDir('playwright');
} catch (e) {
  console.error(JSON.stringify({ error: 'playwright_missing', message: e.message, dir: playwrightDir }));
  process.exit(3);
}

const { chromium: playwrightChromium } = playwrightPackage;

const args = process.argv.slice(2);
let specFile = null;
let outDir = null;

// Parse arguments
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--spec' && args[i + 1]) {
    specFile = args[++i];
  } else if (args[i] === '--out' && args[i + 1]) {
    outDir = args[++i];
  }
}

if (!specFile || !outDir) {
  console.error(JSON.stringify({ error: 'invalid_args', message: 'Missing --spec or --out' }));
  process.exit(2);
}

// Read and validate spec
let spec;
try {
  const specData = readFileSync(specFile, 'utf8');
  spec = JSON.parse(specData);
} catch (e) {
  console.error(JSON.stringify({ error: 'read_spec', message: e.message, file: specFile }));
  process.exit(2);
}

// Validate spec structure
const requiredFields = ['pages', 'viewports'];
for (const field of requiredFields) {
  if (!spec[field] || !Array.isArray(spec[field])) {
    console.error(JSON.stringify({ error: 'invalid_spec', message: `Missing or invalid ${field}` }));
    process.exit(2);
  }
}

// Default timeout
const timeoutMs = spec.timeout_ms || 30000;

// Browser launch args (from reference prototype)
const launchArgs = [
  '--font-render-hinting=none',
  '--disable-gpu',
  '--force-color-profile=srgb',
  '--hide-scrollbars'
];

// Validate viewport names match pattern ^[0-9]{2,5}x[0-9]{2,5}$
function isValidViewportName(name) {
  return /^[0-9]{2,5}x[0-9]{2,5}$/.test(name);
}

// Validate name pattern: ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$
function isValidName(name) {
  return /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(name);
}

// Validate URL patterns
function isValidUrl(url) {
  return url.startsWith('http://127.0.0.1') ||
         url.startsWith('http://localhost') ||
         url.startsWith('file://');
}

// Validate selectors (non-empty, no control characters)
function isValidSelector(selector) {
  if (!selector || typeof selector !== 'string') return false;
  return !/[\x00-\x1F\x7F]/.test(selector);
}

// Validate mutation id pattern
function isValidMutationId(id) {
  if (!id || typeof id !== 'string') return false;
  return /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(id);
}

// Validate mutation op
function isValidMutationOp(op) {
  return ['set-text', 'set-style', 'set-attr', 'remove'].includes(op);
}

// Validate mutation
function validateMutation(mutation) {
  const errors = [];

  if (!mutation || typeof mutation !== 'object') {
    errors.push('Mutation is not an object');
    return errors;
  }

  // Required fields
  if (!mutation.id || typeof mutation.id !== 'string') {
    errors.push('Mutation missing or invalid id');
  } else if (!isValidMutationId(mutation.id)) {
    errors.push(`Invalid mutation id: ${mutation.id}`);
  }

  if (!mutation.selector || typeof mutation.selector !== 'string') {
    errors.push('Mutation missing or invalid selector');
  } else if (!isValidSelector(mutation.selector)) {
    errors.push(`Invalid mutation selector: ${mutation.selector}`);
  }

  if (!mutation.op || typeof mutation.op !== 'string') {
    errors.push('Mutation missing or invalid op');
  } else if (!isValidMutationOp(mutation.op)) {
    errors.push(`Invalid mutation op: ${mutation.op}`);
  }

  // Value validation based on op
  if (mutation.op === 'set-text') {
    if (!mutation.value || typeof mutation.value !== 'string') {
      errors.push('Mutation with op set-text missing or invalid value');
    } else if (mutation.value.length > 500) {
      errors.push(`Mutation value exceeds 500 characters: ${mutation.value.length}`);
    }
  } else if (mutation.op === 'set-style') {
    if (!mutation.value || typeof mutation.value !== 'string') {
      errors.push('Mutation with op set-style missing or invalid value');
    } else {
      const disallowed = ['{', '}', '<', '>', 'url('];
      for (const char of disallowed) {
        if (mutation.value.includes(char)) {
          errors.push(`Mutation value contains disallowed sequence: ${char}`);
          break;
        }
      }
    }
  } else if (mutation.op === 'set-attr') {
    if (!mutation.value || typeof mutation.value !== 'string') {
      errors.push('Mutation with op set-attr missing or invalid value');
    } else {
      if (!mutation.value.includes('=')) {
        errors.push(`Mutation value must be name=value format: ${mutation.value}`);
      } else {
        const [name] = mutation.value.split('=');
        const allowedNames = ['aria-label', 'title', 'alt', 'class'];
        if (!allowedNames.includes(name) && !name.startsWith('data-')) {
          errors.push(`Mutation has disallowed attribute name: ${name}`);
        }
      }
    }
  } else if (mutation.op === 'remove') {
    if (mutation.value !== undefined) {
      errors.push('Mutation with op remove must not have value');
    }
  }

  // Index validation
  if (mutation.index !== undefined) {
    if (mutation.index !== 'all' && (typeof mutation.index !== 'number' || mutation.index < 0 || !Number.isInteger(mutation.index))) {
      errors.push(`Mutation index must be non-negative integer or 'all': ${mutation.index}`);
    }
  }

  return errors;
}

// Check analysis function
async function runChecks(page, viewport, masks) {
  const maskSelector = masks.length > 0 ? masks.join(',') : null;

  const result = await page.evaluate(({ vpWidth, vpHeight, maskSelector, FINDINGS_MAX }) => {
    // Contrast and path helpers (defined in the page context)
    const parse = (c) => { const m = c.match(/^rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)$/); return m ? [+m[1], +m[2], +m[3], m[4] === undefined ? 1 : +m[4]] : null; };
    const over = (top, bottom) => { const a = top[3]; return [0, 1, 2].map((i) => top[i] * a + bottom[i] * (1 - a)).concat([1]); };
    const lum = ([r, g, b]) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
    const ratio = (a, b) => { const [hi, lo] = lum(a) >= lum(b) ? [lum(a), lum(b)] : [lum(b), lum(a)]; return (hi + 0.05) / (lo + 0.05); };
    const getEffectiveBackground = (el) => {
      let current = el;
      const colors = [];
      while (current && current !== document.documentElement) {
        const style = window.getComputedStyle(current);
        const bgColor = style.backgroundColor;
        const bgImage = style.backgroundImage;
        if (bgImage && bgImage !== 'none') return null; // Cannot compute
        const parsed = parse(bgColor);
        if (parsed && parsed[3] !== 0) {
          colors.push(parsed);
        }
        current = current.parentElement;
      }
      // Default to white if no background found
      if (colors.length === 0) return [255, 255, 255, 1];
      // Composite from the outermost layer inward: colors were collected innermost first
      let result = [255, 255, 255, 1];
      for (let i = colors.length - 1; i >= 0; i--) {
        result = over(colors[i], result);
      }
      return result;
    };
    const getAncestorPath = (el) => {
      const path = [];
      let current = el;
      let depth = 0;
      while (current && current !== document.documentElement && depth < 5) {
        const tag = current.tagName.toLowerCase();
        const id = current.id ? '#' + current.id : '';
        let selector = tag + id;
        if (!id && current.classList.length > 0) {
          selector += '.' + current.classList[0];
        }
        if (current.parentElement) {
          const siblings = Array.from(current.parentElement.children).filter(c => c.tagName === current.tagName);
          const idx = siblings.indexOf(current);
          if (idx > 0) selector += ':nth-of-type(' + (idx + 1) + ')';
        }
        path.unshift(selector);
        current = current.parentElement;
        depth++;
      }
      return path.join(' > ');
    };

    const elements = document.querySelectorAll('*');
    const findings = [];
    const summary = {
      contrast: 0,
      'clipped-text': 0,
      'page-overflow-x': 0,
      'missing-accessible-name': 0,
      'placeholder-text': 0,
      'implausible-timestamp': 0,
      'tiny-text': 0,
      'repeated-unavailable': 0
    };
    let elementsExamined = 0;
    let pageHeight = document.documentElement.scrollHeight;
    let viewportHeight = vpHeight;
    const unavailableCount = (document.body.innerText.match(/Unavailable/gi) || []).length;
    let contrastUnknown = 0;
    let pageOverflowX = false;

    // Check for page overflow x
    if (document.documentElement.scrollWidth > vpWidth + 1) {
      pageOverflowX = true;
      summary['page-overflow-x'] = 1;
      findings.push({
        check: 'page-overflow-x',
        severity: 'warning',
        selector: 'document.documentElement',
        box: { x: 0, y: 0, width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight },
        text: null,
        detail: `scrollWidth ${document.documentElement.scrollWidth} exceeds viewport width ${vpWidth}`
      });
    }

    // Placeholder text patterns
    const placeholderPatterns = [
      'undefined',
      'null',
      'nan',
      '[object object]',
      'todo',
      'tbd',
      'lorem ipsum'
    ];
    const placeholderRegex = /\{\{.*?\}\}|%[sd]|\$\{.*?\}/i;

    // Timestamp patterns
    const epochPattern = /1970-01-01/;
    const yearsAgoPattern = /\b(\d{2,}) years? ago\b/i;

    // Iterate through all elements
    const SKIP_TAGS = new Set(['HTML', 'HEAD', 'SCRIPT', 'STYLE', 'TITLE', 'META', 'LINK', 'NOSCRIPT', 'TEMPLATE']);

    // Elements whose visible text repeats 'Unavailable' three or more times, keeping only
    // the deepest containers so ancestors do not repeat the same finding.
    const unavailableDeepest = new Map();
    {
      const candidates = [];
      for (const el of elements) {
        if (SKIP_TAGS.has(el.tagName)) continue;
        if (maskSelector) {
          try { if (el.closest(maskSelector)) continue; } catch (e) { /* invalid selector reported elsewhere */ }
        }
        const inner = el.innerText;
        if (!inner) continue;
        const count = (inner.match(/Unavailable/gi) || []).length;
        if (count >= 3) candidates.push({ el, count });
      }
      for (const c of candidates) {
        if (!candidates.some((o) => o.el !== c.el && c.el.contains(o.el))) {
          unavailableDeepest.set(c.el, c.count);
        }
      }
    }

    for (const el of elements) {
      if (SKIP_TAGS.has(el.tagName)) continue;
      const style = window.getComputedStyle(el);

      // Skip non-rendered elements
      if (style.display === 'none' ||
          style.visibility === 'hidden' ||
          style.opacity === '0') {
        continue;
      }

      // Skip zero-size elements
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) {
        continue;
      }

      // Skip masked elements
      if (maskSelector) {
        try {
          if (el.closest(maskSelector)) continue;
        } catch (e) {
          // Invalid selector already reported
        }
      }

      elementsExamined++;

      // Get ancestor path for selector
      const selector = getAncestorPath(el);

      // Own text: this element's direct text nodes only (descendants are examined on their own)
      const ownText = Array.from(el.childNodes)
        .filter((n) => n.nodeType === 3)
        .map((n) => n.nodeValue)
        .join(' ')
        .replace(/\s+/g, ' ')
        .trim();
      const text = ownText.length > 0 ? ownText : null;
      // Rendered text of the whole subtree, used only where containers matter
      const subtreeText = el.innerText ? el.innerText.trim() : '';

      // 1. Contrast check
      if (text && text.length > 0) {
        const textColor = style.color;
        const textColorParsed = parse(textColor);

        if (textColorParsed) {
          const bgColor = getEffectiveBackground(el);
          if (bgColor) {
            const r = ratio(textColorParsed, bgColor);
            const fontSize = parseFloat(style.fontSize) || 0;
            const fontWeight = parseInt(style.fontWeight) || 400;

            // Large text: >= 24px or >= 18.66px with weight >= 700
            const isLargeText = fontSize >= 24 || (fontSize >= 18.66 && fontWeight >= 700);
            const threshold = isLargeText ? 3.0 : 4.5;

            if (r < threshold) {
              summary.contrast++;
              findings.push({
                check: 'contrast',
                severity: 'warning',
                selector: selector,
                box: { x: Math.round(rect.left), y: Math.round(rect.top), width: Math.round(rect.width), height: Math.round(rect.height) },
                text: text.slice(0, 80),
                detail: `contrast ratio ${r.toFixed(2)} (threshold: ${threshold})`
              });
            }
          } else {
            contrastUnknown++;
          }
        } else {
          contrastUnknown++;
        }
      }

      // 2. Clipped text check
      if (subtreeText.length > 0) {
        const overflowX = style.overflowX;
        const overflowY = style.overflowY;
        const textOverflow = style.textOverflow;
        const scrollWidth = el.scrollWidth;
        const scrollHeight = el.scrollHeight;
        const clientWidth = el.clientWidth;
        const clientHeight = el.clientHeight;

        let isClipped = false;

        if ((overflowX === 'hidden' || overflowX === 'clip' || overflowY === 'hidden' || overflowY === 'clip') &&
            (scrollWidth > clientWidth + 1 || scrollHeight > clientHeight + 1)) {
          isClipped = true;
        }

        if (textOverflow === 'ellipsis' && scrollWidth > clientWidth + 1) {
          // Check if text is actually truncated
          if (el.scrollWidth > el.clientWidth + 1) {
            isClipped = true;
          }
        }

        if (isClipped) {
          summary['clipped-text']++;
          findings.push({
            check: 'clipped-text',
            severity: 'warning',
            selector: selector,
            box: { x: Math.round(rect.left), y: Math.round(rect.top), width: Math.round(rect.width), height: Math.round(rect.height) },
            text: subtreeText.slice(0, 80),
            detail: 'text overflow hidden'
          });
        }
      }

      // 3. Missing accessible name check
      const tag = el.tagName.toLowerCase();
      let needsAccessibleName = false;

      if (tag === 'button' ||
          (tag === 'a' && el.hasAttribute('href')) ||
          (tag === 'img' && !el.hasAttribute('alt')) ||
          (tag === 'input' && el.type !== 'hidden') ||
          tag === 'select' ||
          tag === 'textarea' ||
          el.getAttribute('role') === 'button' ||
          el.getAttribute('role') === 'link') {
        needsAccessibleName = true;
      }

      if (needsAccessibleName) {
        let hasAccessibleName = false;

        // Check various sources of accessible name
        if (tag === 'img') {
          // alt attribute (empty is allowed)
          if (el.hasAttribute('alt')) {
            hasAccessibleName = true;
          }
        } else if (tag === 'input') {
          if (el.type === 'button' || el.type === 'submit' || el.type === 'reset') {
            // Use value attribute for button-type inputs
            if (el.value && el.value.trim()) {
              hasAccessibleName = true;
            }
          } else if (el.hasAttribute('aria-label') || el.hasAttribute('aria-labelledby') ||
                     el.title || el.value) {
            hasAccessibleName = true;
          }
        } else {
          if (el.hasAttribute('aria-label') || el.hasAttribute('aria-labelledby') ||
              el.title || (el.textContent && el.textContent.trim().length > 0) || el.value ||
              el.querySelector('img[alt]:not([alt=""]), svg title, [aria-label]')) {
            hasAccessibleName = true;
          }
        }

        // Check for a wrapping label
        if (!hasAccessibleName) {
          const wrapping = el.closest('label');
          if (wrapping && wrapping.textContent && wrapping.textContent.trim()) {
            hasAccessibleName = true;
          }
        }

        // Check for associated label
        if (!hasAccessibleName) {
          const id = el.id;
          if (id) {
            const label = document.querySelector(`label[for="${id}"]`);
            if (label && label.textContent && label.textContent.trim()) {
              hasAccessibleName = true;
            }
          }
        }

        if (!hasAccessibleName) {
          summary['missing-accessible-name']++;
          findings.push({
            check: 'missing-accessible-name',
            severity: 'warning',
            selector: selector,
            box: { x: Math.round(rect.left), y: Math.round(rect.top), width: Math.round(rect.width), height: Math.round(rect.height) },
            text: text ? text.slice(0, 80) : null,
            detail: 'missing accessible name'
          });
        }
      }

      // 4. Placeholder text check
      if (text && text.length > 0) {
        const lowerText = text.toLowerCase();
        let isPlaceholder = false;

        for (const pattern of placeholderPatterns) {
          if (lowerText === pattern) {
            isPlaceholder = true;
            break;
          }
        }

        if (!isPlaceholder && placeholderRegex.test(text)) {
          isPlaceholder = true;
        }

        if (isPlaceholder) {
          summary['placeholder-text']++;
          findings.push({
            check: 'placeholder-text',
            severity: 'error',
            selector: selector,
            box: { x: Math.round(rect.left), y: Math.round(rect.top), width: Math.round(rect.width), height: Math.round(rect.height) },
            text: text.slice(0, 80),
            detail: 'placeholder text detected'
          });
        }
      }

      // 5. Implausible timestamp check
      if (text && text.length > 0) {
        let isImplausible = false;

        if (epochPattern.test(text)) {
          isImplausible = true;
        }

        const yearsMatch = text.match(yearsAgoPattern);
        if (yearsMatch && parseInt(yearsMatch[1]) >= 20) {
          isImplausible = true;
        }

        if (isImplausible) {
          summary['implausible-timestamp']++;
          findings.push({
            check: 'implausible-timestamp',
            severity: 'error',
            selector: selector,
            box: { x: Math.round(rect.left), y: Math.round(rect.top), width: Math.round(rect.width), height: Math.round(rect.height) },
            text: text.slice(0, 80),
            detail: 'implausible timestamp'
          });
        }
      }

      // 6. Tiny text check
      if (text && text.length > 0) {
        const fontSize = parseFloat(style.fontSize) || 0;
        if (fontSize < 10) {
          summary['tiny-text']++;
          findings.push({
            check: 'tiny-text',
            severity: 'info',
            selector: selector,
            box: { x: Math.round(rect.left), y: Math.round(rect.top), width: Math.round(rect.width), height: Math.round(rect.height) },
            text: text.slice(0, 80),
            detail: `font size ${fontSize}px`
          });
        }
      }

      // 7. Repeated unavailable check (deepest containers only)
      if (unavailableDeepest.has(el)) {
        summary['repeated-unavailable']++;
        findings.push({
          check: 'repeated-unavailable',
          severity: 'info',
          selector: selector,
          box: { x: Math.round(rect.left), y: Math.round(rect.top), width: Math.round(rect.width), height: Math.round(rect.height) },
          text: subtreeText.slice(0, 80),
          detail: `contains 'Unavailable' ${unavailableDeepest.get(el)} times`
        });
      }

    }

    // Counts in the summary always cover the whole page; only the findings list is capped
    const truncated = findings.length > FINDINGS_MAX;
    if (truncated) {
      findings.length = FINDINGS_MAX;
    }

    const pageHeightRatio = pageHeight > 0 && viewportHeight > 0
      ? Math.round((pageHeight / viewportHeight) * 100) / 100
      : 0;

    return {
      version: 1,
      stats: {
        elements_examined: elementsExamined,
        page_height: pageHeight,
        viewport_height: viewportHeight,
        page_height_ratio: pageHeightRatio,
        unavailable_count: unavailableCount,
        contrast_unknown: contrastUnknown,
        truncated: truncated
      },
      summary: Object.fromEntries(Object.entries(summary).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))),
      findings: findings
    };
  }, { vpWidth: viewport.width, vpHeight: viewport.height, maskSelector: maskSelector, FINDINGS_MAX: FINDINGS_MAX });

  return result;
}

// Validate spec before launching browser
const errors = [];
for (const page of spec.pages) {
  if (!isValidName(page.name)) {
    errors.push(`Invalid page name: ${page.name}`);
  }
  if (!isValidUrl(page.url)) {
    errors.push(`Invalid URL: ${page.url}`);
  }
}

for (const vp of spec.viewports) {
  if (!isValidViewportName(vp.name)) {
    errors.push(`Invalid viewport name: ${vp.name}`);
  }
  if (typeof vp.width !== 'number' || typeof vp.height !== 'number') {
    errors.push(`Invalid viewport dimensions: ${vp.name}`);
  }
}

if (spec.masks) {
  for (const mask of spec.masks) {
    if (!isValidSelector(mask)) {
      errors.push(`Invalid mask selector: ${mask}`);
    }
  }
}

if (spec.checks !== undefined) {
  if (typeof spec.checks !== 'boolean') {
    errors.push(`Invalid checks value: must be boolean, got ${typeof spec.checks}`);
  }
}

// Validate mutations if present
if (spec.mutations && Array.isArray(spec.mutations)) {
  if (spec.mutations.length > 50) {
    errors.push(`Too many mutations: ${spec.mutations.length} (max 50)`);
  }

  const seenIds = new Set();
  for (let i = 0; i < spec.mutations.length; i++) {
    const mutation = spec.mutations[i];
    const mutErrors = validateMutation(mutation);
    errors.push(...mutErrors);

    // Check for duplicate ids
    if (mutation.id && !seenIds.has(mutation.id)) {
      seenIds.add(mutation.id);
    } else if (mutation.id) {
      errors.push(`Duplicate mutation id: ${mutation.id}`);
    }
  }
}

if (errors.length > 0) {
  console.error(JSON.stringify({ error: 'spec_validation', errors }));
  process.exit(2);
}

// Blocked request tracking
const blockedUrls = new Set();

// Check configuration
const CHECKS_DEFAULT = true;
const FINDINGS_MAX = 200;

async function run() {
  let browser;
  try {
    browser = await playwrightChromium.launch({ args: launchArgs });
  } catch (e) {
    console.error(JSON.stringify({ error: 'browser_launch', message: e.message }));
    process.exit(3);
  }

  const results = [];
  let pageFailure = false;

  try {
    // Create the output directory
    const outPath = outDir;

    for (const pageSpec of spec.pages) {
      const pageName = pageSpec.name;
      const pageUrl = pageSpec.url;

      // Create page output directory
      const pageOutDir = `${outPath}/${pageName}`;
      try {
        mkdirSync(pageOutDir, { recursive: true });
      } catch (e) {
        console.error(JSON.stringify({ error: 'mkdir', message: e.message, dir: pageOutDir }));
        process.exit(2);
      }

      for (const viewport of spec.viewports) {
        const vpName = viewport.name;
        const viewportObj = {
          width: viewport.width,
          height: viewport.height
        };

        try {
          const result = await renderPage(
            browser,
            pageUrl,
            viewportObj,
            vpName,
            pageName,
            pageOutDir,
            spec.masks || [],
            spec.mutations || [],
            timeoutMs,
            spec.checks !== undefined ? spec.checks : CHECKS_DEFAULT
          );
          results.push(result);
        } catch (e) {
          if (e instanceof MutationError) {
            console.error(JSON.stringify({ ...e.payload, page: pageName, viewport: vpName }));
          } else {
            console.error(JSON.stringify({ error: 'page_render', page: pageName, viewport: vpName, message: e.message }));
          }
          pageFailure = true;
        }
      }
    }
  } finally {
    try {
      await browser.close();
    } catch (e) {
      // Ignore close errors
    }
  }

  // Print summary line
  const summary = {
    pages: results.length,
    blocked_urls: Array.from(blockedUrls),
    failures: pageFailure ? 1 : 0
  };
  console.log(JSON.stringify(summary));

  if (pageFailure) {
    process.exit(4);
  }
  process.exit(0);
}

async function renderPage(browser, url, viewport, vpName, pageName, outDir, masks, mutations, timeoutMs, doChecks = CHECKS_DEFAULT) {
  const ctx = await browser.newContext({
    viewport: viewport,
    deviceScaleFactor: 1,
    locale: 'en-US',
    timezoneId: 'UTC',
    colorScheme: 'light',
    reducedMotion: 'reduce'
  });

  // Network restriction: allow the target's own origin plus file:, data: and blob:
  const targetOrigin = url.startsWith('file:') ? null : new URL(url).origin;
  await ctx.routeWebSocket(/.*/, (ws) => {
    blockedUrls.add(ws.url());
    ws.close();
  });
  await ctx.route('**/*', async (route) => {
    const urlStr = route.request().url();
    let allowed = urlStr.startsWith('file:') || urlStr.startsWith('data:') || urlStr.startsWith('blob:');
    if (!allowed && targetOrigin) {
      try {
        allowed = new URL(urlStr).origin === targetOrigin;
      } catch (e) {
        allowed = false;
      }
    }
    if (allowed) {
      await route.continue();
    } else {
      blockedUrls.add(urlStr);
      await route.abort();
    }
  });

  const page = await ctx.newPage();

  try {
    await page.goto(url, { waitUntil: 'networkidle', timeout: timeoutMs });
  } catch (e) {
    await ctx.close();
    throw new Error(`Failed to load page: ${e.message}`);
  }

  // Apply styling to disable animations/transitions
  await page.addStyleTag({
    content: '*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}'
  });

  // Wait for fonts and settle
  try {
    await page.evaluate(() => document.fonts && document.fonts.ready);
  } catch (e) {
    // Ignore font ready errors
  }

  await page.waitForTimeout(300);

  // Apply masks (hide elements)
  if (masks.length > 0) {
    const maskStyles = masks.map(selector =>
      `${selector}{visibility:hidden!important}`
    ).join('\n');

    try {
      await page.addStyleTag({ content: maskStyles });
    } catch (e) {
      // Ignore mask errors
    }
  }

  // Apply mutations
  const appliedMutations = [];
  if (mutations && mutations.length > 0) {
    for (const mutation of mutations) {
      const { id, selector, op, value, index } = mutation;

      // Find all matching elements
      const elements = await page.$$(selector);
      const matchedCount = elements.length;

      // Check if we found any elements
      if (matchedCount === 0) {
        throw new MutationError({
          error: 'mutation_no_match',
          mutation_id: id,
          selector: selector,
          message: `Selector "${selector}" matched no elements`
        });
      }

      // Determine which index to apply mutation to
      let targetIndex;
      if (index === 'all') {
        targetIndex = null; // Apply to all
      } else if (typeof index === 'number') {
        if (index < 0 || index >= matchedCount) {
          throw new MutationError({
            error: 'mutation_index_out_of_range',
            mutation_id: id,
            selector: selector,
            index: index,
            matched: matchedCount,
            message: `Index ${index} out of range for selector "${selector}" (found ${matchedCount} elements)`
          });
        }
        targetIndex = index;
      } else {
        // Default index is 0
        targetIndex = 0;
      }

      // Apply the mutation to the selected element(s). Values are passed as
      // arguments: the page context cannot see variables from this scope.
      const targets = targetIndex === null ? elements : [elements[targetIndex]];
      if (op === 'set-text') {
        for (const handle of targets) {
          await handle.evaluate((node, text) => { node.textContent = text; }, value);
        }
      } else if (op === 'set-style') {
        // Add declarations without clobbering existing inline styles
        const declarations = value
          .split(';')
          .map((d) => d.trim())
          .filter(Boolean)
          .map((d) => {
            const colon = d.indexOf(':');
            return [d.slice(0, colon).trim(), d.slice(colon + 1).trim()];
          });
        for (const handle of targets) {
          await handle.evaluate((node, decls) => {
            for (const [prop, val] of decls) {
              node.style.setProperty(prop, val, 'important');
            }
          }, declarations);
        }
      } else if (op === 'set-attr') {
        const eq = value.indexOf('=');
        const attrName = value.slice(0, eq);
        const attrValue = value.slice(eq + 1);
        for (const handle of targets) {
          await handle.evaluate((node, pair) => {
            node.setAttribute(pair.name, pair.value);
          }, { name: attrName, value: attrValue });
        }
      } else if (op === 'remove') {
        for (const handle of targets) {
          await handle.evaluate((node) => { node.remove(); });
        }
      }

      // Record the mutation with matched count
      appliedMutations.push({
        id: id,
        selector: selector,
        op: op,
        index: index === undefined ? 0 : index,
        matched: matchedCount
      });
    }
  }

  // Checks analysis (after mutations, before screenshot)
  let checksOutput = null;
  if (doChecks) {
    const checksResult = await runChecks(page, viewport, masks);
    checksOutput = checksResult;
  }

  // Screenshot
  const pngBuffer = await page.screenshot({ fullPage: false });

  // Calculate SHA256
  const pngHash = createHash('sha256').update(pngBuffer).digest('hex');

  // Text content
  const textContent = await page.evaluate(() => document.body.innerText || '');
  const textHash = createHash('sha256').update(textContent).digest('hex');

  // ARIA snapshot
  const ariaContent = await page.locator('body').ariaSnapshot();
  const ariaText = JSON.stringify(ariaContent);
  const ariaHash = createHash('sha256').update(ariaText).digest('hex');

  // Write outputs
  writeFileSync(`${outDir}/${vpName}.png`, pngBuffer);
  writeFileSync(`${outDir}/${vpName}.text.txt`, textContent);
  writeFileSync(`${outDir}/${vpName}.aria.txt`, ariaText);

  // Layout analysis
  const layoutData = await page.evaluate(({ vpWidth, vpHeight, maskSelector }) => {
    const elements = document.querySelectorAll('*');
    const overflowing = [];

    for (const el of elements) {
      // Masked regions are volatile by definition; keep them out of the layout report
      if (maskSelector) {
        try {
          if (el.closest(maskSelector)) continue;
        } catch (e) {
          // an invalid selector was already reported when masks were applied
        }
      }
      const rect = el.getBoundingClientRect();
      const style = window.getComputedStyle(el);

      // Check for overflow
      const scrollWidth = el.scrollWidth;
      const scrollHeight = el.scrollHeight;
      const clientWidth = el.clientWidth;
      const clientHeight = el.clientHeight;

      let isOverflowing = false;

      // Check if scroll dimensions exceed client dimensions
      if (scrollWidth > clientWidth + 1 || scrollHeight > clientHeight + 1) {
        if (style.overflow !== 'visible') {
          isOverflowing = true;
        }
      }

      // Check if element extends beyond viewport
      if (rect.right > vpWidth || rect.bottom > vpHeight) {
        isOverflowing = true;
      }

      if (isOverflowing) {
        overflowing.push({
          tag: el.tagName.toLowerCase(),
          id: el.id || null,
          classes: el.classList.length > 0 ? Array.from(el.classList) : null,
          box: {
            x: Math.round(rect.left),
            y: Math.round(rect.top),
            width: Math.round(rect.width),
            height: Math.round(rect.height)
          },
          text: el.textContent ? el.textContent.slice(0, 80) : null
        });
      }

      if (overflowing.length >= 50) {
        break;
      }
    }

    return overflowing;
  }, { vpWidth: viewport.width, vpHeight: viewport.height, maskSelector: masks.join(',') });

  const layoutJson = {
    viewport: { width: viewport.width, height: viewport.height },
    elements: layoutData
  };
  writeFileSync(`${outDir}/${vpName}.layout.json`, JSON.stringify(layoutJson, null, 2));

  // Meta information
  const browserInfo = await browser.version();
  const playwrightVersion = requireFromPinnedDir('playwright/package.json').version;

  const metaJson = {
    sha256_png: pngHash,
    playwright_version: playwrightVersion,
    browser_version: browserInfo,
    launch_args: launchArgs,
    viewport: { width: viewport.width, height: viewport.height },
    masks: masks,
    mutations: appliedMutations,
    url: url,
    blocked_urls: Array.from(blockedUrls),
    checks: doChecks
  };
  writeFileSync(`${outDir}/${vpName}.meta.json`, JSON.stringify(metaJson, null, 2));

  // Checks output
  if (checksOutput !== null) {
    writeFileSync(`${outDir}/${vpName}.checks.json`, JSON.stringify(checksOutput, null, 2));
  }

  await ctx.close();

  return {
    page: pageName,
    viewport: vpName,
    hash: pngHash,
    text_hash: textHash,
    aria_hash: ariaHash,
    size_bytes: pngBuffer.length
  };
}

// Run the main function
run().catch((e) => {
  console.error(JSON.stringify({ error: 'uncaught', message: e.message }));
  process.exit(1);
});
