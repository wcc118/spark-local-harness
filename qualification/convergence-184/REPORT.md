# Issue 184: publication and convergence qualification

## Decision

**Functional workflow qualification passed for the candidate. Hold general
production promotion; frontier economy qualification remains incomplete.**
The candidate completed review → correction → publication → independent exact-head
approval. The baseline produced correct code, but both reviews failed to finalize
under the installed two-minute deadline. This is a convergence/lifecycle finding,
not proof of superior model intelligence or lower frontier cost.

No production installation, routing change, harness implementation change, merge,
or flight-loads development occurred. This branch adds qualification evidence only.
The unchanged candidate is PR #185 at `d565b6c`; source/config/agent artifacts are
bound by its `candidate-final-3.json`. Ambient OpenCode inputs were hashed before
execution and verified unchanged afterward. Frozen baseline files were also verified.

## Comparable work and observed results

Issue #186 held the same explicit acceptance criteria for both arms. Both source
clones started at `d818f0eb38c8cbb6f1f4afe58f058a255cc068dd`, tree
`9a37c64548bacdcdac46916ccca3d8072883ed19`. A simulated worker clone could resolve
`origin/main` and the comparison base before inference. The initial reviews used
the same PR #187, exact head, and byte-identical task packet. Corrections used the
same packet and starting tree; final reviews used the same review packet against
each arm's independently produced head. Routes remained Coder-Next/Qwen Code and
DeepSeek V4 Flash 0731/OpenCode. No task-deadline overrides were supplied.

| Stage | Installed baseline | Frozen candidate |
|---|---|---|
| Initial review | Correct REQUEST_CHANGES text, but exit 124; no finalized artifact/publication; checkout deleted | Exit 0; observed tests and probes; published REQUEST_CHANGES and status/blocked |
| Local correction | Exit 0; published `e2372eb` to existing PR #187; status/review | Exit 0; created PR #188 at `f34203b`; status/review |
| Host acceptance | 28 supplied tests and **18/18 independent checks passed** | 28 supplied tests and **18/18 independent checks passed** |
| Final review | Exit 75; incomplete, wall-time-budget; no final verdict/publication | Exit 0; 28 reviewer-executed tests plus focused checks; published APPROVE for exact `f34203b`; status/review-passed |

Codex inspected both implementation/test diffs and independent host results. Both
repairs correctly replace non-finite-query None with ValueError and add NaN/±inf
regressions, without weakening finite out-of-domain tests. Codex used the baseline's
schema-valid initial finding as a correction input despite its failed execution;
this was an explicit primary-agent recovery, not a successful baseline review.
The candidate supplied a completed, published finding. No implementation was
written or repaired by Codex during these trials.

The baseline coder's deterministic postflight retried a stale GitHub head **read**
once and then confirmed publication. It did not repeat the push. All six execution
handles were consumed and acknowledged, including both baseline failures. Trial
identities, exact artifacts, hashes, runtime usage, and handoffs are in `results.json`.

## Frontier measurements — not a savings claim

Six non-overlapping intervals contained qualification supervision, worker startup,
verification, and result consumption. No unrelated implementation work ran inside
them. These were **qualification-only intervals in the existing long conversation**,
not the dedicated fresh Codex session requested for clean economy qualification.
Preparation and work between intervals are excluded; these are not the total cost
of issue #184. `frontier-intervals.jsonl` retains scalar source timestamps/counters.

| Frontier tokens across three measured stages | Baseline | Candidate |
|---|---:|---:|
| Uncached input | 12,353 | 12,796 |
| Output | 3,527 | 3,097 |
| Uncached input + output | **15,880** | **15,893** |
| Cached input | 3,446,400 | 2,733,184 |
| Total including cached input | 3,462,280 | 2,749,077 |

Reasoning output is already included in output totals; it is not added again.
The candidate used about 20.6% fewer total tokens **in these observed intervals**,
almost entirely from less cached context processing. Fresh input plus output was
essentially equal. Neither token category can be converted into a dollar-saving
claim without model/cache pricing and a controlled comparison.

Important confounders: the baseline initial review used extra yielded-cell resume
calls before polling was simplified; baseline coding bore Qwen cold startup while
candidate final review bore the longer DeepSeek cold startup; the baseline updated
an existing PR while the candidate created one; the conversation/cache evolved;
and this was only one pair. The baseline also failed both review stages, so the
arms did not deliver equivalent completed workflows. No causal savings percentage
or local-versus-frontier-only cost ratio is established.

Local usage is separate: baseline coding reported 218,240 runtime tokens; candidate
coding 203,563. Candidate initial/final reviews reported 87,833 / 59,780, including
cache. Baseline initial review has 17,551 partial tokens; final review usage is
unknown. The old aggregate ledger printed zero for some runs, but retained runtime
evidence does not support treating those values as zero spend.

## Historical freeze adjudication

All nine v0.6.0 hashes match immutable commit
`114d6ce6f8020d580e3b734b2adf80f4ca7818f5`. See `historical-freeze.json`.
The known maintained-test failure compares historical digests with mutable current
files. It is adjudicated here as a pre-existing test/provenance-boundary problem,
not a candidate regression. No expected hash or assertion was changed. #176 remains
the separate test repair. The prior candidate suite evidence remains 625 passed /
1 known failure and benchmark 5 passed; those suites were not needlessly rerun for
this evidence-only branch. This pass independently executed each fixture's 28 tests
and the 18-check acceptance oracle, plus revision/artifact/label checks.

## Remaining gate and durable continuation

Keep PR #185 uninstalled/unmerged pending review. Functional publication no longer
needs another open-ended debugging loop. The remaining economy question needs a
fresh Codex session, identical polling policy, controlled startup accounting, and
explicit full-task frontier attribution (including preparation/recovery). Use the
frozen artifacts, packets and revision identities here; do not replay this chat.
Do not claim that this session met the fresh-session criterion. Issue #184 remains
open for that gate and the promotion decision. Flight-loads stays paused. Fixture
PRs are closed without merge after recording their terminal states.
