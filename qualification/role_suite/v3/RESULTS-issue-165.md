# Issue 165: DeepSeek prompt and role qualification

## Decision

DeepSeek V4 Flash is qualified for selective local offload, not as a general coder or
sole merge gate. Use suite-specific prompts:

- reviewer: `role-contract-plus-uncertainty`
- non-coding analysis: `role-contract-plus-uncertainty`
- investigation: `structured-v1`
- test strategy: `structured-v1`
- workflow orchestration: do not route to DeepSeek

The selected hybrid scored 53/62 (85.5%), versus 47/62 for the best single
DeepSeek policy and 45/62 for the best Coder-Next policy in the v2 baseline.

## Prompt ablation

The challenge set contains the 15 observed DeepSeek structured-v2 misses, two
ambiguous clean controls, two additional clean controls, and eleven passing
anchors. Each of 28 cases was run three times under four policies (336 attempts).
All attempts completed with zero timeouts and zero tool errors.

| Policy | Correct | Rate | Elapsed (s) | Tokens |
| --- | ---: | ---: | ---: | ---: |
| structured-v1 | 49/84 | 58.3% | 1587.0 | 1,995,992 |
| role-contract | 50/84 | 59.5% | 1908.0 | 2,068,141 |
| role-contract-plus-verification | 50/84 | 59.5% | 2200.8 | 2,108,169 |
| role-contract-plus-uncertainty | 53/84 | 63.1% | 1923.4 | 2,096,534 |

No global prompt cleared the promotion gate. Uncertainty improved reviewer and
non-coding work, but verification did not justify its latency and token cost.
Structured-v1 remained better for investigation and test strategy. All policies
scored 0/12 on workflow cases, so prompt engineering did not repair that role.

Additional observations:

- promotion score excluding ambiguous controls: uncertainty 50/78, verification
  50/78, role contract 49/78, structured control 48/78
- reviewer score excluding ambiguous controls: uncertainty 15/15
- reviewer false positives on unambiguous clean controls: 0/6 for every
  role-specific policy; structured control 1/6
- outcome-stable case/policy groups: uncertainty 24/28, role contract 23/28,
  structured control 21/28, verification 20/28

## Full-corpus confirmation

The winning policy was selected independently per suite and then run once over all
62 frozen v2 cases.

| Suite | Selected policy | Correct | Elapsed (s) | Tokens |
| --- | --- | ---: | ---: | ---: |
| reviewer | uncertainty | 19/20 raw; 18/18 unambiguous | 441.018 | 508,483 |
| non-coding | uncertainty | 21/24 | 414.629 | 579,126 |
| investigator | structured-v1 | 8/8 | 135.997 | 187,792 |
| test strategy | structured-v1 | 5/6 | 174.736 | 154,584 |
| workflow | structured-v1 | 0/4 | 81.388 | 94,833 |
| **Total** | hybrid | **53/62** | **1247.768** | **1,524,818** |

All 62 attempts completed with zero timeouts and zero tool errors. The remaining
test-strategy miss was `untested-branch`. Workflow remained a categorical failure.

## Repository shadow review

Five historical commits were reviewed independently: two defect-bearing commits
and three clean controls. Both models received the same bounded prompt for the
reported comparison (360-second process timeout, nominal maximum 12 tool calls).

| Commit | Oracle | DeepSeek reasoning | Coder-Next |
| --- | --- | --- | --- |
| `624b18a` | wrong miss set, wrong role routing, fake integration | no final verdict; 20 tool calls | CLEAN |
| `fba248f` | identity/runtime, aggregation/runtime, fabricated metric, runpy warning | fabricated metric | CLEAN |
| `d4227f3` | CLEAN | CLEAN; 25 tool calls | CLEAN; 23 tool calls |
| `9cbdb88` | CLEAN | CLEAN; 10 tool calls | CLEAN; 17 tool calls |
| `a6eaa63` | CLEAN | CLEAN; 8 tool calls | CLEAN; 6 tool calls |

Exact commit-level verdict accuracy was 3/5 for each model. That tie hides the
important operational difference: DeepSeek recovered one of seven oracle defect
labels under the bounded prompt while Coder-Next recovered none. In an earlier
unbounded DeepSeek pass, it recovered all three prototype defects and the
fabricated metric (4/7), but it also produced a 96-call no-verdict loop on a clean
commit. The nominal tool-call instruction was not reliably obeyed by either model.

This is evidence for DeepSeek as a high-recall shadow reviewer whose findings are
adjudicated by Codex, not evidence that it should be the sole reviewer. Coder-Next
also should not be the sole reviewer: it confidently marked both defect-bearing
commits clean.

## Operational recommendation

Use DeepSeek for bounded reviewer, investigation, and analytical jobs where the
result can be checked against an oracle, tests, or a primary-agent adjudication.
Use the uncertainty contract for reviews and non-coding synthesis. Keep explicit
wall-clock and tool-call guards in the harness, record incomplete/no-verdict runs,
and do not penalize slower successful work merely for exceeding an interactive
latency target. Do not route autonomous workflow control or final merge authority
to this model.

Retained raw artifacts are under
`~/.local/share/codex-local-harness/benchmark-165/`:

- `prompt-ablation-v3/`
- `full-hybrid-confirmation/`
- `shadow-reviews/`
