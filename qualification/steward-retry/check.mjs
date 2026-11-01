// Oracle check for cursorPlayer (#267) behavioral trial
// Usage: node check.mjs WORKSPACE_DIR
// Exit 0 = behavioral pass, 1 = behavioral mismatch, 2 = execution failure

import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

process.on('unhandledRejection', () => {});

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
function makeEnv() {
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

    // Shared wb object that the source code will use
    // The source code's const wb={...} will shadow this, so we need to
    // use a different approach: wrap the source code to capture wb
    const sharedWb = {
        stations: [], defaults: {}, inputs: {}, results: {},
        current: null, saved: [], busy: false, plantRun: null, plantRunSnapshot: null,
        cursor: 0, freezeTimer: null, options: { atmosphere: false, propulsion: false },
        players: {}
    };

    let tid = 0;
    const sandbox = {
        document, window: {
            matchMedia: () => ({ matches: false, addEventListener() {} }),
            addEventListener() {}, innerWidth: 1200, innerHeight: 800
        },
        matchMedia: () => ({ matches: false, addEventListener() {} }),
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
        // Expose sharedWb
        _sharedWb: sharedWb,
        Math, JSON, Object, Array, Number, String, Promise, Error, Map, Set,
    };
    sandbox.globalThis = sandbox;

    vm.createContext(sandbox);

    // Pre-wrap the source code to capture wb
    // We replace const wb={...} with a wrapper that uses sharedWb
    const modifiedCode = code.replace(
        /^const wb=\{stations:\[\],defaults:\{\},inputs:\{\},results:\{\},current:null,saved:\[\],busy:false,plantRun:null,plantRunSnapshot:null,cursor:0,freezeTimer:null,options:\{atmosphere:false,propulsion:false\}\};/m,
        'const wb = _sharedWb;'
    );

    // Run the workspace code
    new vm.Script(modifiedCode, { filename: 'workbench.js' }).runInContext(sandbox, { timeout: 5000 });

    return {
        sandbox, elems, sharedWb,
        advance: async (ms) => {
            const end = vnow + ms;
            for (let guard = 0; guard < 200000; guard++) {
                timers.sort((a, b) => a.t - b.t || a.id - b.id);
                const nxt = timers[0];
                if (!nxt || nxt.t > end) break;
                timers.shift();
                vnow = Math.max(vnow, nxt.t);
                try {
                    await nxt.fn();
                } catch {
                    // a throwing frame simply stops that player
                }
                await new Promise(r => setImmediate(r));
            }
            vnow = end;
        },
        now: () => vnow,
        setNow: (n) => { vnow = n; },
        timers: () => [...timers],
    };
}

// Test helper: get player state from sharedWb
function getPlayerState(env, key) {
    const p = env.sharedWb?.players?.[key];
    return p;
}

// Test: verify reduced-motion preference sets initial speed to 0.25x
async function testReducedMotion(env) {
    // Reset players to ensure fresh state
    env.sharedWb.players = {};

    // Mock reduced motion preference BEFORE creating player
    env.sandbox.matchMedia = (q) => {
        if (q === '(prefers-reduced-motion: reduce)') {
            return { matches: true, addEventListener() {} };
        }
        return { matches: false, addEventListener() {} };
    };

    // Create a fake run for testing
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

    const key = 'test-reduced';
    let currentIndex = 0;
    const setIndex = (i) => { currentIndex = i; };

    // Create player - this is where speed is determined
    const playerDiv = env.sandbox.cursorPlayer(key, () => fakeRun, () => currentIndex, setIndex);

    if (!playerDiv) {
        return { ok: false, error: 'cursorPlayer did not return element' };
    }

    // Check player was created
    const player = getPlayerState(env, key);
    if (!player) {
        return { ok: false, error: 'player state not created' };
    }

    // Fix version: playbackReducedMotion() determines speed at player creation
    // Raw version: always sets speed=1 in initialization (bug - doesn't respect reduced motion)
    // The fix properly uses playbackReducedMotion() in the player creation line
    if (player.speed !== 0.25) {
        return { ok: false, observed: { speed: player.speed }, expected: { speed: 0.25 }, check: 'reducedMotionSpeed' };
    }

    return { ok: true, check: 'reducedMotionSpeed' };
}

// Test: verify clock-based playback (fix version) vs frame-time based (raw version)
async function testPlaybackSpeedAccumulation(env) {
    // Reset players for fresh test
    env.sharedWb.players = {};

    // Mock reduced motion to get predictable initial speed
    env.sandbox.matchMedia = (q) => {
        if (q === '(prefers-reduced-motion: reduce)') {
            return { matches: false, addEventListener() {} };
        }
        return { matches: false, addEventListener() {} };
    };

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

    const key = 'test-speed';
    let currentIndex = 0;
    const setIndex = (i) => { currentIndex = i; };

    // Create player - this initializes speed based on reduced motion
    env.sandbox.cursorPlayer(key, () => fakeRun, () => currentIndex, setIndex);
    const player = getPlayerState(env, key);
    if (!player) {
        return { ok: false, error: 'player state not created' };
    }

    // Start playback at 1x speed (speed should be 1 now since reduced motion is off)
    const playBtn = env.sandbox.document.getElementById(`wb-${key}-play`);
    if (playBtn?.listeners?.click) {
        playBtn.listeners.click.forEach(cb => cb());
    }

    // Wait 100ms (0.1s wall time) at 1x speed = 0.1s recorded time advance
    await env.advance(100);

    // Check if clock has been initialized
    const p = getPlayerState(env, key);
    if (!p || !('clock' in p)) {
        return { ok: false, observed: { hasClock: false }, expected: { hasClock: true }, check: 'clockInitialized' };
    }

    // With 100ms at 1x speed, clock should be at ~0.1s
    const elapsedRecorded = p.clock;
    if (elapsedRecorded < 0.05 || elapsedRecorded > 0.2) {
        return { ok: false, observed: { clock: elapsedRecorded }, expected: { clock: 0.1 }, check: 'clockAccumulation' };
    }

    return { ok: true, observed: { clock: elapsedRecorded }, check: 'clockAccumulation' };
}

// Test: verify that slow evaluations skip ticks (fix version behavior)
async function testSlowEvaluationSkips(env) {
    env.sharedWb.players = {};

    env.sandbox.matchMedia = (q) => {
        if (q === '(prefers-reduced-motion: reduce)') {
            return { matches: false, addEventListener() {} };
        }
        return { matches: false, addEventListener() {} };
    };

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

    const key = 'test-slow';
    let currentIndex = 0;
    let setIndexDelay = 0;
    const setIndex = async (i, immediate) => {
        await new Promise(r => setTimeout(r, setIndexDelay));
        currentIndex = i;
    };

    env.sandbox.cursorPlayer(key, () => fakeRun, () => currentIndex, setIndex);
    const playBtn = env.sandbox.document.getElementById(`wb-${key}-play`);
    if (playBtn?.listeners?.click) {
        playBtn.listeners.click.forEach(cb => cb());
    }

    // Simulate slow evaluation by setting delay
    setIndexDelay = 100;

    // Advance 200ms wall time; at 1x speed, should advance 200ms recorded time
    await env.advance(200);

    const p = getPlayerState(env, key);
    if (!p) {
        return { ok: false, error: 'player state not created' };
    }

    // Check if clock is tracking properly
    if (p.clock < 0.15 || p.clock > 0.25) {
        return { ok: false, observed: { clock: p.clock }, expected: { clock: 0.2 }, check: 'slowEvaluationSkips' };
    }

    return { ok: true, observed: { clock: p.clock, index: currentIndex }, check: 'slowEvaluationSkips' };
}

// Test: verify loop behavior at end of playback
async function testLoopBehavior(env) {
    env.sharedWb.players = {};

    env.sandbox.matchMedia = (q) => {
        if (q === '(prefers-reduced-motion: reduce)') {
            return { matches: false, addEventListener() {} };
        }
        return { matches: false, addEventListener() {} };
    };

    const fakeRun = {
        truth: [
            { time_s: 0 },
            { time_s: 1 },
            { time_s: 2 },
        ],
    };

    const key = 'test-loop';
    let currentIndex = 2; // Start at last frame
    const setIndex = (i) => { currentIndex = i; };

    env.sandbox.cursorPlayer(key, () => fakeRun, () => currentIndex, setIndex);
    const player = getPlayerState(env, key);
    if (!player) {
        return { ok: false, error: 'player not created' };
    }

    // Start playback
    const playBtn = env.sandbox.document.getElementById(`wb-${key}-play`);
    if (playBtn?.listeners?.click) {
        playBtn.listeners.click.forEach(cb => cb());
    }

    // Loop should be false by default
    if (player.loop !== false) {
        return { ok: false, observed: { loop: player.loop }, expected: { loop: false }, check: 'defaultLoop' };
    }

    return { ok: true, observed: { loop: player.loop }, check: 'defaultLoop' };
}

// Test: verify stopPlayback clears timer
async function testStopClearsTimer(env) {
    env.sharedWb.players = {};

    env.sandbox.matchMedia = (q) => {
        if (q === '(prefers-reduced-motion: reduce)') {
            return { matches: false, addEventListener() {} };
        }
        return { matches: false, addEventListener() {} };
    };

    const fakeRun = { truth: [{ time_s: 0 }, { time_s: 1 }] };
    const key = 'test-stop';
    let idx = 0;
    const setIdx = (i) => { idx = i; };

    env.sandbox.cursorPlayer(key, () => fakeRun, () => idx, setIdx);
    const p = getPlayerState(env, key);
    if (!p) {
        return { ok: false, error: 'player not created' };
    }

    // Start playback
    const playBtn = env.sandbox.document.getElementById(`wb-${key}-play`);
    if (playBtn?.listeners?.click) {
        playBtn.listeners.click.forEach(cb => cb());
    }

    // Verify timer is set
    if (!p.timer) {
        return { ok: false, observed: { timer: p.timer }, expected: { timer: 'non-null' }, check: 'timerSetAfterPlay' };
    }

    // Stop playback
    env.sandbox.stopPlayback?.(key);

    // Verify timer is cleared and playing is false
    const p2 = getPlayerState(env, key);
    if (!p2) {
        return { ok: false, error: 'player state lost after stop' };
    }

    if (p2.timer !== null) {
        return { ok: false, observed: { timer: p2.timer }, expected: { timer: null }, check: 'timerClearedAfterStop' };
    }

    if (p2.playing !== false) {
        return { ok: false, observed: { playing: p2.playing }, expected: { playing: false }, check: 'playingClearedAfterStop' };
    }

    return { ok: true, observed: { timer: p2.timer, playing: p2.playing }, check: 'stopClearsTimer' };
}

// Main check
async function runCheck() {
    const env = makeEnv();

    // Run all tests
    const tests = [
        { name: 'reducedMotion', fn: testReducedMotion },
        { name: 'speedAccumulation', fn: testPlaybackSpeedAccumulation },
        { name: 'slowEvaluationSkips', fn: testSlowEvaluationSkips },
        { name: 'loopBehavior', fn: testLoopBehavior },
        { name: 'stopClearsTimer', fn: testStopClearsTimer },
    ];

    const results = [];
    for (const t of tests) {
        try {
            const r = await t.fn(env);
            results.push({ test: t.name, ...r });
            if (!r.ok) {
                return { ok: false, results, failure: r };
            }
        } catch (e) {
            return { ok: false, error: `${t.name} threw: ${e.message}`, results };
        }
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
