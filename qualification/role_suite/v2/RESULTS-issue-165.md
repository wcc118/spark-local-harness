# Issue #165 role assessment

Run date: 2026-09-04 (America/New_York)

Models were executed sequentially through OpenCode. DeepSeek used the pinned
`deepseek-v4-flash-0731-exl3-k2` EXL3 K2 candidate with reasoning enabled,
1,000,000-token configured context, and GPU memory utilization 0.85. Coder-Next
used `Qwen/Qwen3-Coder-Next-FP8`, 185,000-token context, and GPU memory
utilization 0.87. Every role-suite case had a 600-second ceiling. There were no
timeouts.

| Model / policy | Correct | Reviewer | Investigator | Test strategy | Non-coding | Workflow | Seconds |
|---|---:|---:|---:|---:|---:|---:|---:|
| Coder-Next / neutral | 45/62 | 15/20 | 5/8 | 4/6 | 21/24 | 0/4 | 678.468 |
| Coder-Next / structured | 40/62 | 14/20 | 5/8 | 3/6 | 18/24 | 0/4 | 546.352 |
| DeepSeek / neutral | 46/62 | 18/20 | 7/8 | 1/6 | 20/24 | 0/4 | 1313.725 |
| DeepSeek / structured | 47/62 | 18/20 | 8/8 | 3/6 | 18/24 | 0/4 | 1180.876 |

All 248 attempts terminated normally. DeepSeek had zero tool errors in both
conditions; Coder-Next had two in each condition. Reported total-token counts
were similar (1.47M-1.52M), while DeepSeek took 1.74x-1.94x as long.

Two frozen reviewer clean controls were ambiguous. Excluding those two cases,
DeepSeek scored 18/18 under both policies; Coder-Next scored 15/18 neutral and
14/18 structured. This sensitivity result strengthens, rather than creates, the
reviewer finding because both models were penalized by the same questionable
controls in the neutral comparison.

## Decision

DeepSeek qualifies as the preferred bounded reviewer and investigator candidate,
using its structured four-phase policy and a generous background-job timeout.
It does not qualify as a wholesale Coder-Next replacement. Coder-Next remains
preferred for test-strategy work and had slightly better non-coding accuracy
under neutral prompting. Neither model earned a workflow-orchestration claim
from this suite, and the workflow controls are too synthetic to support one.

Production routing is not changed automatically. Recommended deployment is a
shadow reviewer/investigator route first, with independent coder input, retained
artifacts, external tests/oracles, and escalation on ambiguous or high-risk work.
The earlier coding benchmark remains complementary evidence: DeepSeek reasoning
was oracle-correct on 9/10 coding cases but completed only 6/10 under the original
policy; the structured policy then completed 12/12 repeated formerly problematic
cases. Together the results show strong capability plus prompt-sensitive stopping,
not general frontier equivalence.
