# Captured flight-loads failures and rejected local repair

**Decision: reject the candidate; retain the replay.** This directory is an experiment, not a production flight-loads implementation. `candidate.py` deliberately preserves the locally delivered candidate unchanged. The accepted production implementation remains in flight-loads PR324.

## Observed cycle

The frozen source came from flight-loads correction commit `5d075c2662def5039f409b18ac5fb81c407857bf`. It failed both development checks and all six evaluation probes. One local attempt (`20261101T034503Z-2159522`, about 13 minutes) passed both development checks and five of six probes, but still claimed root loads for a missing `flight_condition`. Six probes include one development overlap, not six independent held-out tasks. The accepted reference passed all six. Two injected behavioral defects were rejected and one equivalent implementation accepted before dispatch.

`recurrence.py` publishes the escaped condition after evaluation. It must never be counted as fresh held-out evidence in a future attempt. `outcome.json` records the rejection, source identity, evidence counts, limitations and intervention. The source snapshot is compact and hash-pinned; no branch checkout or network fetch is required to replay it.

## Replay

Requires Python3.11+, numpy and scipy. Run from the repository root:

```
python3 qualification/flight-replay/replay.py
python3 qualification/flight-replay/recurrence.py
python3 -m pytest -q tests/test_flight_replay.py qualification/flight-replay/test_candidate.py
```

The first command passes the two development checks. The second intentionally exits1, reproducing the escaped failure. The tests verify this rejected state and distinguish fixture/import failures (exit2) from a behavioral mismatch (exit1). `--candidate PATH` evaluates another module against the same supplied package in a temporary directory.

To replay the baseline, extract only `src/flightloads/aircraft_design_compare.py` from baseline.zip to a scratch path and pass it using `--candidate`. It fails both development checks. Private evaluation scripts and mutation evidence remain in the operator evaluation store; they were not visible to the coder. The original task and checks remain frozen.

## Test infrastructure correction

The worker ran its two tests in separate subprocesses. Standard pytest initially failed1/2 because cached package modules referenced an already-deleted temporary extraction. Primary changed only the test entry points to preserve that process isolation under pytest; all original assertions remain. No candidate repair followed the independent evaluation.

This is a within-case repair result, not a model success rate or proof that executable guidance caused the improvement. Other original pilot defects are out of scope. There is no production route, physics, acceptance policy or permission change.

## Original worker notes (not independent acceptance evidence)

# Flight-Replay Development Checks

This directory contains bounded behavioral development checks for the `candidate.py` module.

## Purpose

Verify that `candidate.py` correctly implements:
1. **Caller-input immutability**: The `compare_designs` function must not mutate caller-owned input on success or rejection.
2. **Invalid-definition row isolation**: Invalid aircraft definitions must result in row-level failures that retain a row for every configuration/case with null unavailable root loads, null corresponding deltas, and meaningful reasons. Other valid configurations must still evaluate.

## Test Results

Both development checks pass:

```json
{
  "status": "passed",
  "checks": [
    {
      "check": "input_unchanged",
      "status": "passed"
    },
    {
      "check": "invalid_definition_isolated",
      "status": "passed"
    }
  ]
}
```

## Observed Results

### Check: input_unchanged

**What it verifies:**
- After calling `compare_designs`, the original spec dictionary passed by the caller remains unchanged.
- Specifically, `spec['configurations'][0]['definition']` must be the exact same object reference as before.
- `definition['components']` must also be the exact same list object reference.
- The JSON serialization of the spec must remain identical.

**Implementation details fixed:**
1. Removed mutation of `cfg['definition']` during validation in `_validate_input_spec`.
2. Removed mutation of case values (`speed_ft_s`, `load_factor`) during validation.
3. Changed validation to use deep copies internally without storing back to the spec.

### Check: invalid_definition_isolated

**What it verifies:**
- When a configuration has an invalid definition (e.g., `flight_condition=None`), processing continues for all configurations.
- Invalid rows produce `null` for all metric values.
- Invalid rows have `load_status='invalid-definition'` with meaningful reasons.
- Valid rows continue to evaluate normally.

**Implementation details fixed:**
1. Removed upfront definition validation in `_validate_input_spec` (which would fail the entire comparison).
2. Added try/except around `_evaluate_configuration` calls in `compare_designs` to catch invalid definitions.
3. Invalid definitions now produce rows with null metrics and explanation reasons.

## Rationale

### Input Immutability

The original implementation had two mutation points:

1. **Definition mutation**: Line `cfg['definition'] = deepcopy(cfg['definition'])` replaced the caller's definition object with a copy, breaking object identity.

2. **Case mutation**: Lines like `case['speed_ft_s'] = _validate_number(...)` converted integers to floats in-place, changing the spec's JSON serialization.

Both violations violated the principle that comparison functions should be read-only with respect to caller data.

### Row-Level Isolation

The original implementation validated all definitions upfront in `_validate_input_spec`. When a definition was invalid, the entire comparison failed with a ValueError, preventing any results from being produced.

The fix defers validation to row evaluation and catches exceptions, allowing the comparison to proceed for valid configurations while reporting meaningful errors for invalid ones.

## Limitations

- This is a bounded development check set, not a comprehensive test suite.
- The checks focus on the two specified behaviors; other edge cases may not be covered.
- No production physics or solver changes were made; only behavioral corrections.
- The baseline is hash-pinned source; fixtures and manifest are not edited.
