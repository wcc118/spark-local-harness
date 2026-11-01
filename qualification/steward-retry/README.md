# cursorPlayer Behavioral Trial (#257)

This directory contains the behavioral test implementation for cursorPlayer playback timing fix (issue #257, source: flight-loads issue267).

## Problem Description

The `cursorPlayer` function in `workbench.js` had a bug in how it handled reduced-motion preference and playback speed accumulation:

1. **Reduced Motion Bug**: The raw version checked `matchMedia` but only applied reduced speed if `player.speed === undefined`. Since the player was always initialized with `speed: 1`, this check never worked, and the playback always ran at full speed regardless of user preference.

2. **Speed Accumulation Bug**: The raw version used `lastFrameTime` for elapsed time calculation without properly accumulating sub-frame time. This led to inaccurate playback timing.

## Fix Applied

The fix version (fix version) addresses these issues:

1. Uses `playbackReducedMotion()` helper function that is called at player creation time
2. Implements clock-based playback with proper time accumulation using `clock` (recorded time) and `wall` (wall time) separately
3. Uses a `token` mechanism to prevent stale timers from updating state
4. Implements `playbackIndexAt()` to correctly find the playback index based on accumulated clock time

## Files

- `task.md` - Original task description
- `template.mjs` - Template file with VM/DOM scaffolding (starting point)
- `check.mjs` - Final behavioral test implementation
- `input/raw/src/flightloads/static/workbench.js` - Raw (buggy) version
- `input/fix/src/flightloads/static/workbench.js` - Fixed version

## Testing

### Reproduction Commands

```bash
# Test against raw (buggy) version - should fail (exit 1)
node check.mjs input/raw

# Test against fix (correct) version - should pass (exit 0)
node check.mjs input/fix
```

### Expected Behavior

**Raw version (fails):**
- `speed` is always 1, ignoring `prefers-reduced-motion` setting
- No proper clock-based time accumulation
- Timer management uses `lastFrameTime` without proper sub-frame accumulation

**Fix version (passes):**
- `speed` is 0.25 when `prefers-reduced-motion: reduce` is matched
- Clock-based playback with proper time accumulation
- Token-based timer management to prevent stale state updates

### Measured Outcomes

```
Raw version (exit 1):
{
  "ok": false,
  "results": [
    {
      "test": "reducedMotion",
      "ok": false,
      "observed": { "speed": 1 },
      "expected": { "speed": 0.25 },
      "check": "reducedMotionSpeed"
    }
  ],
  "failure": {
    "ok": false,
    "observed": { "speed": 1 },
    "expected": { "speed": 0.25 },
    "check": "reducedMotionSpeed"
  }
}

Fix version (exit 0):
{
  "ok": true,
  "results": [
    {
      "test": "reducedMotion",
      "ok": true,
      "check": "reducedMotionSpeed"
    },
    {
      "test": "speedAccumulation",
      "ok": true,
      "observed": { "clock": 0.1 },
      "check": "clockAccumulation"
    },
    {
      "test": "slowEvaluationSkips",
      "ok": true,
      "observed": { "clock": 0.2, "index": 1 },
      "check": "slowEvaluationSkips"
    },
    {
      "test": "loopBehavior",
      "ok": true,
      "observed": { "loop": false },
      "check": "defaultLoop"
    },
    {
      "test": "stopClearsTimer",
      "ok": true,
      "observed": { "timer": null, "playing": false },
      "check": "stopClearsTimer"
    }
  ]
}
```

## Technical Details

### VM Setup

The test uses Node.js `vm` module to execute the workspace JavaScript in a sandboxed environment with:

- DOM stubs (createElement, addEventListener, etc.)
- Virtual clock using a custom `Date` class that returns a controllable timestamp
- Timer stubs that use the virtual clock for scheduling
- Mock `matchMedia` for reduced-motion preference testing

### Key Behavioral Differences

1. **Player Initialization**:
   - Raw: `wb.players[key] = { speed: 1, ... }` then checks matchMedia (too late)
   - Fix: `wb.players[key] ??= { speed: playbackReducedMotion() ? 0.25 : 1, ... }` (correct order)

2. **Playback Loop**:
   - Raw: Uses `lastFrameTime` to calculate elapsed time
   - Fix: Uses `clock` (accumulated recorded time) and `wall` (wall time) separately

3. **Timer Management**:
   - Raw: No token mechanism, stale timers can update state
   - Fix: Uses `token` to ensure only current timer updates state

4. **Index Calculation**:
   - Raw: Uses `nextPlaybackIndex()` which calculates based on current index
   - Fix: Uses `playbackIndexAt()` which finds index based on accumulated clock time
