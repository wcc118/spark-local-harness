// Oracle check for cursorPlayer (#257) behavioral trial
// Usage: node check.mjs WORKSPACE_DIR
// Exit 0 = all behavioral checks pass, 1 = behavioral mismatch, 2 = execution failure

import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

// Exceptions should propagate - don't suppress unhandled rejections
// process.on('unhandledRejection', () => {});

const workspace = process.argv[2];
if (!workspace) {
    console.log(JSON.stringify({ ok: false, error: 'usage: check.mjs WORKSPACE' }));
    process.exit(2);
}

function out(result, code) {
    console.log(JSON.stringify(result));
    process.exit(code);
}

// Load workspace source
let code;
try {
    code = fs.readFileSync(path.join(workspace, 'src/flightloads/static/workbench.js'), 'utf8');
} catch (e) {
    out({ ok: false, error: `cannot read workspace file: ${e.message}` }, 2);
}

// Create a sandboxed VM context with DOM and virtual clock stubs
// NOTE: We do NOT rewrite the source code - execute it byte-for-byte via vm.Script
function makeEnv(reducedMotion = false) {
    let vnow = 1_700_000_000_000;
    const timers = [];
    const elems = new Map();

    const mkEl = (tag) => {
        const e = {
            tag, children: [], attrs: {}, listeners: {}, style: {}, dataset: {},
            textContent: '', innerHTML: '', className: '', value: '', checked: false,
            classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
            setAttribute(k, v) { this.attrs[k] = String(v); if (k === 'id') elems.set(String(v), this); },
            getAttribute(k) { return this.attrs[k] ?? null; },
            addEventListener(n, f) { (this.listeners[n] ??= []).push(f); },
            appendChild(c) { this.children.push(c); return c; },
            append(...c) { this.children.push(...c); },
            replaceWith() {}, remove() {}, removeChild() {}, insertBefore() {}, focus() {},
            querySelector: () => null, querySelectorAll: () => [],
        };
        return e;
    };

    const document = {
        createElement: mkEl, createTextNode: t => ({ text: String(t) }),
        body: mkEl('body'),
        getElementById: id => elems.get(id) ?? mkEl('missing'),
        querySelector: () => null, querySelectorAll: () => [],
        addEventListener() {},
    };

    class FakeDate extends Date {
        static now() { return vnow; }
    }

    // Create matchMedia that respects reducedMotion setting
    // This must be set BEFORE loading/creating player for reducedMotion tests
    const matchMediaStub = (q) => {
        if (q === '(prefers-reduced-motion: reduce)') {
            return { matches: reducedMotion, addEventListener() {} };
        }
        return { matches: false, addEventListener() {} };
    };

    let tid = 0;
    const sandbox = {
        document, window: {
            matchMedia: matchMediaStub,
            addEventListener() {}, innerWidth: 1200, innerHeight: 800
        },
        matchMedia: matchMediaStub,
        localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
        fetch: () => new Promise(() => {}),
        console: { log() {}, error() {}, warn() {} },
        Date: FakeDate, performance: { now: () => vnow },
        setTimeout: (fn, ms) => {
            const id = ++tid;
            timers.push({ id, t: vnow + (ms || 0), fn, ms });
            return id;
        },
        clearTimeout: id => {
            const i = timers.findIndex(x => x.id === id);
            if (i >= 0) timers.splice(i, 1);
        },
        setInterval: () => 0, clearInterval() {},
        requestAnimationFrame: fn => sandbox.setTimeout(fn, 16),
        Math, JSON, Object, Array, Number, String, Promise, Error, Map, Set,
    };
    sandbox.globalThis = sandbox;

    vm.createContext(sandbox);

    // Run the workspace code - NO SOURCE REWRITING, execute byte-for-byte
    new vm.Script(code, { filename: 'workbench.js' }).runInContext(sandbox, { timeout: 5000 });

    return {
        sandbox, elems,
        advance: async (ms) => {
            const end = vnow + ms;
            for (let guard = 0; guard < 200000; guard++) {
                timers.sort((a, b) => a.t - b.t || a.id - b.id);
                const nxt = timers[0];
                if (!nxt || nxt.t > end) break;
                timers.shift();
                vnow = Math.max(vnow, nxt.t);
                // DO NOT swallow timer exceptions - let them propagate
                await nxt.fn();
                await new Promise(r => setImmediate(r));
            }
            vnow = end;
        },
        now: () => vnow,
        setNow: (n) => { vnow = n; },
        timers: () => [...timers],
    };
}

// Test: verify selected-speed movement (1x speed)
// The tick should be at or after the clock time
// At 1x speed, 1000ms wall time = 1000ms recorded time = advance from index 0 to index 1
async function testSelectedSpeed(env) {
    const fakeRun = {
        truth: [
            { time_s: 0 },
            { time_s: 1 },
            { time_s: 2 },
            { time_s: 3 },
            { time_s: 4 },
            { time_s: 5 },
        ],
    };

    const key = 'test-selected';
    let currentIndex = 0;
    const setIndex = (i) => { currentIndex = i; };

    // Create player in default (non-reduced-motion) environment
    env.sandbox.cursorPlayer(key, () => fakeRun, () => currentIndex, setIndex);

    // Get the play button and trigger actual listener created by cursorPlayer
    const playBtn = env.sandbox.document.getElementById(`wb-${key}-play`);
    if (!playBtn || !playBtn.listeners?.click) {
        return { ok: false, error: 'play button not found or has no click listener' };
    }

    // Trigger actual play listener
    playBtn.listeners.click.forEach(cb => cb());

    // At 1x speed with tick every 50ms:
    // - First 1000ms: accumulate 20 ticks of 50ms each = 1s elapsed = advance from index 0 to index 1
    // - Next 1000ms: advance from index 1 to index 2
    const observations = [];

    // Wait 1000ms - should advance to index 1
    await env.advance(1000);
    observations.push({ elapsed: 1000, index: currentIndex, expectedIndex: 1 });

    // Wait another 1000ms - should advance to index 2
    await env.advance(1000);
    observations.push({ elapsed: 2000, index: currentIndex, expectedIndex: 2 });

    // Wait another 1000ms - should advance to index 3
    await env.advance(1000);
    observations.push({ elapsed: 3000, index: currentIndex, expectedIndex: 3 });

    // Validate observations - at 1x speed, index should advance to expectedIndex after elapsed ms
    for (const obs of observations) {
        if (obs.index !== obs.expectedIndex) {
            return {
                ok: false,
                observed: { index: obs.index, elapsed: obs.elapsed },
                expected: { expectedIndex: obs.expectedIndex },
                check: 'selectedSpeedMovement'
            };
        }
    }

    return {
        ok: true,
        check: 'selectedSpeedMovement',
        observations
    };
}

// Test: verify slow-speed accumulation (0.25x speed)
// At 0.25x speed, 4000ms wall time = 1000ms recorded time = advance one index
async function testSlowSpeedAccumulation(env) {
    const fakeRun = {
        truth: [
            { time_s: 0 },
            { time_s: 1 },
            { time_s: 2 },
        ],
    };

    const key = 'test-slow';
    let currentIndex = 0;
    const setIndex = (i) => { currentIndex = i; };

    // Create player
    env.sandbox.cursorPlayer(key, () => fakeRun, () => currentIndex, setIndex);

    // Get the speed select and trigger actual listener to set 0.25x
    const speedSelect = env.sandbox.document.getElementById(`wb-${key}-speed`);
    if (!speedSelect || !speedSelect.listeners?.change) {
        return { ok: false, error: 'speed select not found' };
    }

    // Trigger actual speed change listener
    speedSelect.listeners.change.forEach(cb => cb({ target: { value: '0.25' } }));

    // Start playback
    const playBtn = env.sandbox.document.getElementById(`wb-${key}-play`);
    if (!playBtn || !playBtn.listeners?.click) {
        return { ok: false, error: 'play button not found' };
    }
    playBtn.listeners.click.forEach(cb => cb());

    // At 0.25x speed with tick every 50ms:
    // - 4000ms wall time = 1000ms recorded = 1s = advance one index
    const observations = [];

    // Wait 4000ms - should advance to index 1 (0.25 * 4000ms = 1000ms = 1s)
    await env.advance(4000);
    observations.push({ elapsed: 4000, index: currentIndex, expectedIndex: 1 });

    // Wait another 4000ms - should advance to index 2
    await env.advance(4000);
    observations.push({ elapsed: 8000, index: currentIndex, expectedIndex: 2 });

    // Validate observations
    for (const obs of observations) {
        if (obs.index !== obs.expectedIndex) {
            return {
                ok: false,
                observed: { index: obs.index, elapsed: obs.elapsed },
                expected: { expectedIndex: obs.expectedIndex },
                check: 'slowSpeedAccumulation'
            };
        }
    }

    return {
        ok: true,
        check: 'slowSpeedAccumulation',
        observations
    };
}

// Test: verify reduced-motion default sets initial speed to 0.25x
// Use a FRESH environment with reducedMotion=true for each scenario
async function testReducedMotionDefault(env) {
    // env is already created with reducedMotion=true
    const fakeRun = {
        truth: [
            { time_s: 0 },
            { time_s: 1 },
            { time_s: 2 },
        ],
    };

    const key = 'test-reduced';
    let currentIndex = 0;
    const setIndex = (i) => { currentIndex = i; };

    // Create player in reduced motion environment
    // The matchMedia is already set up in makeEnv(reducedMotion=true)
    env.sandbox.cursorPlayer(key, () => fakeRun, () => currentIndex, setIndex);

    // Start playback (default speed should be 0.25 due to reduced motion)
    const playBtn = env.sandbox.document.getElementById(`wb-${key}-play`);
    if (!playBtn || !playBtn.listeners?.click) {
        return { ok: false, error: 'play button not found' };
    }

    playBtn.listeners.click.forEach(cb => cb());

    // At 0.25x speed with tick every 50ms:
    // - 4000ms wall time = 1000ms recorded = 1s = advance one index
    const observations = [];

    await env.advance(4000);
    observations.push({ elapsed: 4000, index: currentIndex, expectedIndex: 1 });

    await env.advance(4000);
    observations.push({ elapsed: 8000, index: currentIndex, expectedIndex: 2 });

    // Validate observations
    for (const obs of observations) {
        if (obs.index !== obs.expectedIndex) {
            return {
                ok: false,
                observed: { index: obs.index, elapsed: obs.elapsed },
                expected: { expectedIndex: obs.expectedIndex },
                check: 'reducedMotionDefault'
            };
        }
    }

    return {
        ok: true,
        check: 'reducedMotionDefault',
        observations
    };
}

// Test: verify pause holds the displayed tick
async function testPause(env) {
    const fakeRun = {
        truth: [
            { time_s: 0 },
            { time_s: 1 },
            { time_s: 2 },
            { time_s: 3 },
            { time_s: 4 },
            { time_s: 5 },
        ],
    };

    const key = 'test-pause';
    let currentIndex = 0;
    const setIndex = (i) => { currentIndex = i; };

    // Create player
    env.sandbox.cursorPlayer(key, () => fakeRun, () => currentIndex, setIndex);

    // Get play button
    const playBtn = env.sandbox.document.getElementById(`wb-${key}-play`);
    if (!playBtn || !playBtn.listeners?.click) {
        return { ok: false, error: 'play button not found' };
    }

    // First click: play
    playBtn.listeners.click.forEach(cb => cb());

    // Record index at 100ms
    const observations = [];
    await env.advance(100);
    observations.push({ action: 'after 100ms play', elapsed: 100, index: currentIndex });

    // Second click: pause
    playBtn.listeners.click.forEach(cb => cb());
    const indexPaused = currentIndex;

    // Advance 200ms during pause - index should NOT change
    await env.advance(200);
    observations.push({ action: 'after 200ms pause', elapsed: 300, index: currentIndex });

    if (currentIndex !== indexPaused) {
        return {
            ok: false,
            observed: { indexPaused, indexAfterPause: currentIndex, advanceDuringPause: 200 },
            expected: { index: 'unchanged during pause' },
            check: 'pause'
        };
    }

    // Third click: resume
    playBtn.listeners.click.forEach(cb => cb());

    // Advance 100ms after resume - index should advance
    await env.advance(100);
    observations.push({ action: 'after 100ms resume', elapsed: 400, index: currentIndex });

    if (currentIndex === indexPaused) {
        return {
            ok: false,
            observed: { indexPaused, indexAfterResume: currentIndex },
            expected: { index: 'advanced after resume' },
            check: 'pauseResume'
        };
    }

    return {
        ok: true,
        check: 'pause',
        observations
    };
}

// Main check
async function runCheck() {
    // Use fresh environment for each test scenario
    // Each scenario gets its own makeEnv call with appropriate reducedMotion setting
    const envSelected = makeEnv(false);  // No reduced motion for selected speed test
    const envSlow = makeEnv(false);      // No reduced motion for slow speed test
    const envReduced = makeEnv(true);    // Reduced motion for reduced motion test
    const envPause = makeEnv(false);     // No reduced motion for pause test

    const results = [];

    // Test 1: selected-speed movement (1x speed)
    const test1 = await testSelectedSpeed(envSelected);
    results.push({ test: 'selectedSpeed', ...test1 });
    if (!test1.ok) {
        return { ok: false, results, failure: test1 };
    }

    // Test 2: slow-speed accumulation (0.25x speed)
    const test2 = await testSlowSpeedAccumulation(envSlow);
    results.push({ test: 'slowSpeedAccumulation', ...test2 });
    if (!test2.ok) {
        return { ok: false, results, failure: test2 };
    }

    // Test 3: reduced-motion default (0.25x initial speed)
    const test3 = await testReducedMotionDefault(envReduced);
    results.push({ test: 'reducedMotionDefault', ...test3 });
    if (!test3.ok) {
        return { ok: false, results, failure: test3 };
    }

    // Test 4: pause holds the displayed tick
    const test4 = await testPause(envPause);
    results.push({ test: 'pause', ...test4 });
    if (!test4.ok) {
        return { ok: false, results, failure: test4 };
    }

    return { ok: true, results };
}

// Run and output
runCheck().then(result => {
    const code = result.ok ? 0 : 1;
    out(result, code);
}).catch(e => {
    out({ ok: false, error: `unhandled: ${e.message}` }, 2);
});
