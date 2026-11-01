// Oracle check skeleton for node-vm runtime
// Usage: node check.mjs WORKSPACE_DIR
// Exit 0 = pass, 1 = fail, 2 = could not run (usage error or runtime error)

import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

process.on('unhandledRejection', () => {});
const workspace = process.argv[2];
const out = (r, code) => { console.log(JSON.stringify(r)); process.exit(code); };

// Usage check: workspace directory is required
if (!workspace) {
    out({ ok: false, error: 'usage: check.mjs WORKSPACE' }, 2);
}

// Load workspace source file
let code;
try {
    code = fs.readFileSync(path.join(workspace, 'src/flightloads/static/workbench.js'), 'utf8');
} catch (e) {
    out({ ok: false, error: `cannot read workspace file: ${e.message}` }, 2);
}

// Create a sandboxed node:vm context with DOM/clock stubs
// Edit these stubs to match your case's requirements
function makeEnv() {
    let vnow = 1_700_000_000_000, tid = 0;
    const timers = [], elems = new Map();

    const mkEl = tag => {
        const e = {
            tag, children: [], attrs: {}, listeners: {}, style: {}, dataset: {}, textContent: '', innerHTML: '',
            className: '', value: '', checked: false,
            classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
            setAttribute(k, v) { this.attrs[k] = String(v); if (k === 'id') elems.set(String(v), this); },
            getAttribute(k) { return this.attrs[k] ?? null; },
            addEventListener(n, f) { (this.listeners[n] ??= []).push(f); },
            appendChild(c) { this.children.push(c); return c; }, append(...c) { this.children.push(...c); },
            replaceWith() {}, remove() {}, removeChild() {}, insertBefore() {}, focus() {},
            querySelector: () => null, querySelectorAll: () => [],
        };
        return e;
    };

    const document = {
        createElement: mkEl, createTextNode: t => ({ text: String(t) }), body: mkEl('body'),
        getElementById: id => elems.get(id) ?? mkEl('missing'), querySelector: () => null, querySelectorAll: () => [],
        addEventListener() {},
    };

    class FakeDate extends Date { static now() { return vnow; } }

    const sandbox = {
        document, window: { matchMedia: () => ({ matches: false, addEventListener() {} }), addEventListener() {}, innerWidth: 1200, innerHeight: 800 },
        matchMedia: () => ({ matches: false, addEventListener() {} }),
        localStorage: { getItem: () => null, setItem() {}, removeItem() {} }, fetch: () => new Promise(() => {}),
        console: { log() {}, error() {}, warn() {} }, Date: FakeDate, performance: { now: () => vnow },
        setTimeout: (fn, ms) => { const id = ++tid; timers.push({ id, t: vnow + (ms || 0), fn }); return id; },
        clearTimeout: id => { const i = timers.findIndex(x => x.id === id); if (i >= 0) timers.splice(i, 1); },
        setInterval: () => 0, clearInterval() {}, requestAnimationFrame: fn => sandbox.setTimeout(fn, 16),
        Math, JSON, Object, Array, Number, String, Promise, Error, Map, Set,
    };
    sandbox.globalThis = sandbox;

    vm.createContext(sandbox);

    // Run the workspace code in the sandbox
    new vm.Script(code, { filename: 'workbench.js' }).runInContext(sandbox, { timeout: 5000 });

    // Return the sandbox for access to the executed code
    return { sandbox, elems, advance: async (ms) => {
        const end = vnow + ms;
        for (let guard = 0; guard < 200000; guard++) {
            timers.sort((a, b) => a.t - b.t || a.id - b.id);
            const nxt = timers[0];
            if (!nxt || nxt.t > end) break;
            timers.shift(); vnow = Math.max(vnow, nxt.t);
            try { await nxt.fn(); } catch { /* a throwing frame simply stops that player */ }
            await new Promise(r => setImmediate(r));
        }
        vnow = end;
    }};
}

// TODO: Implement your check logic here
// The sandbox contains the executed workspace code
// Call the real functions, set up the scenario, and assert expected behavior
async function runCheck() {
    const env = makeEnv();

    // Check that the workspace function is defined
    if (typeof env.sandbox.cursorPlayer !== 'function') {
        return { ok: false, error: 'cursorPlayer not defined' };
    }

    // TODO: Set up your test scenario here
    // - Get references to workspace functions from env.sandbox
    // - Set up the environment (call functions, trigger events)
    // - Verify the expected behavior

    // Example test scenario (replace with your actual test):
    // const result = env.sandbox.someFunction(input);
    // if (result !== expected) return { ok: false, reason: 'unexpected result' };

    // Return success if all checks pass
    return { ok: true, reason: 'check passed' };
}

// Run the check and output result
runCheck().then(result => {
    const code = result.ok ? 0 : 1;
    out(result, code);
}).catch(e => {
    out({ ok: false, error: e.message }, 2);
});
