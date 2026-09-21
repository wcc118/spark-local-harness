# Native Coder and Reviewer Runtime Baseline

Issue: [#153](https://github.com/wcc118/codex-local-harness/issues/153)

Date: 2026-08-26. Model: `Qwen/Qwen3-Coder-Next-FP8`. Server: the same
sequential Spark vLLM endpoint for every attempt. OpenCode was 1.18.21 and
Qwen Code was 0.22.0. Native mode used isolated runtime homes, endpoint-only
configuration, and one shared neutral containment boundary. It did not copy
custom OpenCode agents or select the tailored coder.

## Coding baseline

Three complete ten-case repetitions produced:

| Runtime | Correct | Normal exits | Wall time | Tools | Tool errors |
|---|---:|---:|---:|---:|---:|
| OpenCode | 25/30 | 30/30 | 404.0s | 99 | 1 |
| Qwen Code | 30/30 | 30/30 | 921.0s | 87 | 8 |

OpenCode was 56.1% faster but repeatedly failed test-authoring (3/3), plus one
investigation and one cleanup attempt. Qwen Code passed every attempt; its
test-authoring cases averaged 79.7s versus OpenCode's 18.8s. Runtime-reported
tokens are not comparable: OpenCode reported 1,005,926 and Qwen Code 4,153,513
under different accounting conventions.

Compared with the earlier tailored single runs, native OpenCode changed from
9/10 in 493.0s to 25/30 in 404.0s total, while native Qwen Code changed from
9/10 in 229.3s to 30/30 with a 307.0s per-repetition mean. Repetition and
configuration differences prevent treating those figures as a causal effect
size, but the earlier conclusion that Qwen Code was inherently faster did not
survive the native baseline.

## Reviewer matrix

One independent ten-case review repetition used preserved repetition-1 coder
workspaces. Reviewers saw the task and workspace but not coder reasoning. A
correct verdict approves oracle-passing work and blocks oracle-failing work.

| Pair | Correct verdicts | Code unchanged | Wall time | Errors |
|---|---:|---:|---:|---|
| OO | 9/10 | 9/10 | 399.3s | false block: review; mutated: diagnosis |
| OQ | 8/10 | 10/10 | 382.3s | false blocks: review, hygiene |
| QO | 8/10 | 10/10 | 217.9s | false blocks: diagnosis, review |
| QQ | 8/10 | 10/10 | 405.8s | false blocks: diagnosis, review |

No reviewer falsely approved the one incorrect coder result. OO led verdict
accuracy but violated review-only behavior once. QO was fastest, but did not
improve verdict accuracy. Reviewer conclusions remain preliminary because this
matrix has one repetition and only one negative implementation.

## Decision

Do not change production routing yet. Native Qwen Code is the stronger coder
candidate on correctness; native OpenCode is substantially faster. Repeat the
review matrix with more seeded defects and explicit reviewer acceptance detail
before selecting OO, QO, OQ, or QQ.

Raw coding attempts: `~/.local/share/codex-local-harness/native-coding-20260826-r{1,2,3}/`.
Raw reviews: `~/.local/share/codex-local-harness/native-review-20260826-r1/`.
