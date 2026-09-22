# Phase 1 vision gate (issue #195)

Measures whether Qwen3.8-27B on the RTX 3090 (LM Studio) finds visual defects
that the deterministic checks from `.checks.json` (issue #194) cannot, without
adding unacceptable noise or instability. This directory is the harness: the
gate decision itself is recorded as a comment on #195, not here.

## Layout

- `manifest.json` -- the frozen and dev fixture specs (page, mutations,
  ground-truth defects and, for model-only defects, the keywords `score.py`
  uses to match a model finding to that defect) plus one `external` entry
  documenting a fixture that is not built by `build_fixtures.py`.
- `pages/` -- static HTML fixture pages, hand-authored and self-contained (no
  external network or fonts). `mini-dashboard.html` and `flight-panel.html`
  are mutated by the manifest to seed defects; `status-cards.html` /
  `status-cards-defects.html` and `numeric-reading.html` are used as-is.
- `fixtures/` (gitignored, not committed -- deterministic build output, regenerate with
  `build_fixtures.py`; see step 1 below) --
  `frozen/<id>/`: built by `build_fixtures.py`: the rendered
  screenshot, `.checks.json` (deterministic baseline A), `.meta.json`, and
  `ground_truth.json` (the scored ground truth for that fixture, including a
  copy of `baseline_a_summary` for convenience).
- `fixtures/dev/` -- a couple of fixtures for iterating on the judge prompt.
  Never scored against the gate; keep it separate from `frozen/` so nothing
  frozen gets tuned after seeing results.
- `fixtures/excluded/flight-loads-external/` -- a real screenshot from
  `wcc118/flight-loads` (`results/envelope-browser-final/dashboard.png`),
  tried and excluded: see its `ground_truth.json` note. Kept for reference,
  not part of the frozen set.
- `build_fixtures.py`, `judge.py`, `score.py` -- see below.

## Why these fixtures

The gate specifically wants defects a DOM/CSS analysis cannot catch: a chart
that contradicts its own legend or axis, a total that does not match the
rows it sums, a value that does not match the table next to it, or content
that is misaligned while remaining valid in the DOM. `mini-dashboard.html`
and `flight-panel.html` were built so those relationships are exact and
checkable (bar heights are pixel-proportional to their labelled values, a
line chart's peak pixel position matches its labelled peak time, and so on),
so a single mutation creates one unambiguous, self-contained contradiction
with no external ground truth required to judge it. Two such bugs were found
and fixed by testing the "clean" fixtures against the model before freezing
the set: `mini-dashboard.html`'s original bar heights were not actually
proportional to their labelled values, and `flight-panel.html`'s labelled
peak value (3.319678) exceeded its axis maximum (3.3). Both are recorded in
git history for this directory; the model itself found both.

`status-cards.html` / `status-cards-defects.html` exercise the deterministic
checks from #194 on a page unrelated to the harness's own dashboard, so
Phase 1 is not solely testing on the UI it was designed against.

## Running it

```bash
# 1. Render every fixture (needs the pinned Playwright from #194; see docs/visual-render.md)
PYTHONPATH=src python3 qualification/vision/build_fixtures.py

# 2. Run the pure-logic tests
PYTHONPATH=src python3 -m pytest -q tests/test_vision_gate.py

# 3. Judge every frozen fixture with the 3090 (needs LMSTUDIO_BASE_URL and the
#    model loaded at >=65536 context, per docs/lane.md)
PYTHONPATH=src python3 qualification/vision/judge.py \
  --out ~/.local/share/codex-local-harness/vision-gate/runs/RUN_ID

# 4. Score the run against the gate in #195
PYTHONPATH=src python3 qualification/vision/score.py \
  --run ~/.local/share/codex-local-harness/vision-gate/runs/RUN_ID
```

`judge.py` requires the lane to be healthy before it starts and holds the
3090 lease for the whole run -- it does not fail open the way the
production lane guard does, because a silent skip here would look like an
untested fixture rather than an honest result. Raw model outputs (one JSON
file per fixture per repeat) are written under the run directory, which is
not committed to Git (it lives under `~/.local/share/codex-local-harness/`,
consistent with every other qualification run in this repo).

## Known limitations of this first pass

- Matching a model finding to a ground-truth defect uses a fixed keyword
  list per defect (set when the fixture was authored), not a second model
  judge or exact semantic scoring. It is deterministic and auditable, but a
  correct finding phrased without any of the listed keywords would be
  under-counted; every per-repeat match is recorded in the scored output for
  a human to spot-check.
- 9 model-only defects and 4 clean screenshots is a first, small frozen set,
  not a statistically powerful one.
- "Regression vs. intended change" (a paired before/after judgment) is not
  covered by this fixture set.
- Only viewport-sized (1280x800) screenshots were tested as tractable; see
  `fixtures/excluded/flight-loads-external/ground_truth.json` for why a
  full, un-cropped page screenshot was not.
