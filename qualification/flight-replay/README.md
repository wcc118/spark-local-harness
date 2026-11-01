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
