# Convergence candidate qualification — 2026-09-19

## Disposition

Implementation candidate for #181; **hold production promotion**. The limited
experiment demonstrates retained local work and a useful clarification/restart.
It does not demonstrate frontier cost savings or a clean end-to-end publication
comparison. Follow-up qualification is #184. Flight-loads remains paused.

Codex implemented and reviewed the harness. Local models were synthetic test
subjects only. Coder-Next remained on Qwen Code; DeepSeek V4 Flash 0731 remained on
OpenCode. Production wrappers, routing, and installed source were not replaced.
No model fine-tuning, DeepSeek Harness migration, or custom compaction was added.

## Behavior implemented

- Opt-in `bin/codex-local-worker-candidate`; coder/reviewer whole-task deadlines
  disabled by default. Explicit cancellation/deadlines remain available.
- Attached mechanical observation, private incremental event logs, independent
  ownership and terminal records, caller acknowledgement, retained checkouts.
- Bounded guidance with recorded queued/submitted/model-reported-receipt states;
  checkpoint restart reuses the coder tree with a curated task, not chat replay.
- Explicit investigative reviewer, immutable submitted checkout, writable scratch,
  runtime capability check, exact-head review-to-correction packet validation.
- Repetition warnings remain observable; the reviewer runtime's `doom_loop`
  permission cannot terminate it solely because a command repeated. Codex judges
  progress and intervenes. This is not an autonomous scheduler or a promise that
  a model will never loop.
- Canonical complete/partial/unknown local accounting, cache semantics, deduplicated
  calls/errors, separate frontier assessment intervals. Unknown frontier attribution
  does not render as zero spend or autonomous success.

## Limited comparison

Synthetic issue #182 / draft fixture PR #183. The initial coder tree was
`df67913bec9aaeb831b9ebe8355194feebcbddcc`; reviewer head was
`d818f0eb38c8cbb6f1f4afe58f058a255cc068dd`. Original paired packets are retained
beside this report. The frozen installed baseline is identified by `baseline.json`.
The initial candidate trials were exploratory and not an immutable released build;
final candidate source/config/agent hashes are in `candidate-final-3.json`.

| Trial / run | Observed outcome | Local tokens including cache |
|---|---|---:|
| Baseline coder `20260919T192409Z-547225` | Reported 26 tests; host oracle 17/18; postflight exit 65; unpublished checkout deleted by old cleanup | 186,912 complete |
| Candidate coder `20260919T192700Z-554862` | Reported 20 tests; host oracle 17/18; postflight exit 65; commit and checkout retained | 196,419 complete |
| Guided continuation `20260919T192842Z-556924` | Same retained tree; emitted guidance receipt; reported 23 tests; host oracle **18/18**; exit 0 with publication explicitly disabled | 158,939 complete |
| Baseline reviewer `20260919T193225Z-559774` | Emitted APPROVE prose/result; exit **124** at its default deadline; no finalized/published review | 17,268 partial |
| Exploratory candidate reviewer `20260919T193644Z-574065` | Independently ran 26 tests and probes; APPROVE; wrapper failed after inference; saved result separately validated without publication | 242,146 partial |
| Frozen candidate setup `20260919T194810Z-600561` | Failed before inference on copied read-only template; independent ownership retained failure despite no workflow admission | Unavailable |
| Frozen candidate review `20260919T194847Z-601580` | Scratch access and supplied tests worked; OpenCode rejected its repeated-tool permission; inference exit 1, no verdict | 72,279 partial |
| Final frozen candidate `20260919T195239Z-610142` | Exit **0**; independently ran 26 tests, wrote scratch diagnostic, returned validated **REQUEST_CHANGES** for all three non-finite query cases; submitted source unchanged; publication disabled | 84,801 complete |

The two coder source clones lacked `origin/main`, so deterministic postflight
failed for both. Neither published a coder PR; no publication retry was performed.
The baseline implementation could only be recovered from recorded write-tool
arguments. The candidate retained its actual unpublished commit and working tree.
Codex supplied a missing requirement; the local coder made the repair itself.

**Acceptance caveat:** the original prose required finite queries but did not
explicitly state how non-finite query times must fail. The oracle required
`ValueError`; both first implementations returned `None`. This is evidence of
clarification and recovery, not a defensible model-accuracy ranking. The initial
reviewer comparison is also inconclusive: both treated `None` as acceptable under
that prose. The clarified sensitivity packet explicitly requires rejection of NaN
and both infinities; it is a separate trial, not an identical-task comparison.

## Failures found and handled

1. Candidate scratch exceptions in JSON were overridden by the markdown agent's
   later wildcard denial. Rendered exact-root and descendant exceptions in the
   final agent layer. A direct, no-inference OpenCode/container probe confirmed
   scratch writes succeed and submitted files remain read-only.
2. Codex edited the exploratory wrapper while its shell was running. That caused
   exit 2 after the model finished. The execution remains failed; saved review
   evidence was inspected separately. Subsequent runs use frozen distributions.
3. A frozen read-only template copied its mode into the disposable runtime home.
   Rendering now replaces that runtime copy atomically.
4. OpenCode's repetition heuristic requested `doom_loop` permission, which the
   deny-default role rejected and terminated inference. The candidate now permits
   that heuristic continuation while retaining repetition diagnostics. Host-only
   executable discovery was also removed from investigative packets; a successful
   capability check is reported from the worker container before inference.

Failures remain in the run record; no failed attempt was relabeled a success.

## Frontier accounting

The captured `issue181-review-observation` interval observed 105,887 input tokens,
including 101,888 cached inputs, plus 1,678 output tokens: **107,565 total**, of
which 3,999 input tokens were uncached. Reasoning (75) is a reported output subset,
not added again. This interval also contained implementation work, so it cannot
be attributed to monitoring alone. Comparable baseline frontier intervals were
not captured: baseline-versus-candidate frontier savings are **unknown**.

Mechanical polling invokes no model. The Codex client's tool-wait/wakeup schedule
is unchanged; long-context frontier assessments can still be expensive. Measure
non-overlapping intervals in a dedicated qualification session before making a
leverage or dollar-savings claim. Local token totals are not frontier dollar costs.

## Validation and remaining promotion evidence

- Maintained pytest suite: **625 passed, 1 failed**; the historical
  `test_v060_freeze_binds_paired_benchmark_release` failure is tracked in #176 and
  was independently reproduced on unchanged #177. Frozen hashes were not rewritten.
- Benchmark fixture suite: 5 passed.
- Direct OpenCode/container boundary probe: scratch writable, checkout immutable,
  effective loop permission allowed. Reproduce from repository root with
  `PYTHONPATH=src python3 qualification/convergence/check-reviewer-boundary.py`.
- Regression coverage includes explicit timeout and no-deadline restart, retained
  edits, receipt evidence, foreign ownership, malformed optional results, stale
  exact-head findings, current frontier counter format, and unknown accounting.
- Keep #179 (DeepSeek Harness investigation) and #180 (portable installation)
  separate. #184 tracks publication-capable fixtures, historical freeze disposition,
  clean paired qualification and measured frontier economy before promotion.

All eight real trial handles were consumed and acknowledged, including failures.
The final reviewer scratch copy matched the submitted implementation SHA-256
`82932e28bd7c0d0833042ac9d7ffbdfc05798db45011a049491d0938e0ee421f`.
The exploratory reviewer ran beyond two minutes without a default task cutoff;
the final clarified review completed in about 84 seconds. Duration is descriptive,
not a quality or cost score.
Raw logs and scratch files remain private local evidence; GitHub holds the summary,
revisions, outstanding questions, and next action.
