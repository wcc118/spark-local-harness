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

if (errors.length > 0) {
  console.error(JSON.stringify({ error: 'spec_validation', errors }));
  process.exit(2);
}

// Blocked request tracking
const blockedUrls = new Set();

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
            timeoutMs
          );
          results.push(result);
        } catch (e) {
          console.error(JSON.stringify({ error: 'page_render', page: pageName, viewport: vpName, message: e.message }));
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

async function renderPage(browser, url, viewport, vpName, pageName, outDir, masks, timeoutMs) {
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
  const layoutData = await page.evaluate(({ vpWidth, vpHeight }) => {
    const elements = document.querySelectorAll('*');
    const overflowing = [];

    for (const el of elements) {
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
  }, { vpWidth: viewport.width, vpHeight: viewport.height });

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
    url: url,
    blocked_urls: Array.from(blockedUrls)
  };
  writeFileSync(`${outDir}/${vpName}.meta.json`, JSON.stringify(metaJson, null, 2));

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
