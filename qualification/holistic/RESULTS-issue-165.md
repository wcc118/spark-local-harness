# Issue 165: Structured-issue holistic qualification

## Decision

DeepSeek V4 Flash reasoning is the stronger local offload candidate under the
issue-driven harness tested here. Its correctness-weighted score was **85.53**,
versus **77.71** for Coder-Next. This qualifies DeepSeek for bounded coding,
review, investigation, and non-coding analysis with deterministic verification
or Codex adjudication. It does not qualify either model as a workflow
orchestrator or merge authority.

Every case was delivered as a GitHub-style issue containing objective, context,
scope, acceptance criteria, constraints, verification, durable deliverable, and
escalation conditions. Reusable execution contracts were supplied by the
specialist operation. Hidden oracles were not exposed to either model.

## Locked confirmation scorecard

| Model | Coding | Specialist | Lifecycle | Holistic | Estimated Codex tokens displaced |
| --- | ---: | ---: | ---: | ---: | ---: |
| Coder-Next | 9/10 | 42/62 | 3/5 | 77.71 | 238,500 |
| DeepSeek reasoning | 10/10 | 47/62 | 3/5 | 85.53 | 265,500 |

Holistic correctness weights were coding 50%, specialist 35%, and lifecycle
15%. Displacement is a planning estimate from the frozen assumptions (12,000
tokens per completed-correct coding task, 3,000 per specialist task, and 1,500
per lifecycle task), not observed Codex billing.

| Model / track | Completed-correct | Artifact-correct | Elapsed (s) | Model tokens | Timeouts | Tool calls/errors |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Coder-Next coding | 9/10 | 10/10 | 927.040 | 9,563,756 | 1 | 433/0 |
| DeepSeek coding | 10/10 | 10/10 | 696.951 | 645,462 | 0 | 82/5 |
| Coder-Next specialist | 42/62 | 42/62 | 1,044.317 | 1,678,637 | 0 | 139/1 |
| DeepSeek specialist | 47/62 | 47/62 | 4,748.017 | 1,581,933 | 0 | 123/0 |
| Coder-Next lifecycle | 3/5 | 3/5 | 142.530 | 124,963 | 0 | 10/0 |
| DeepSeek lifecycle | 3/5 | 3/5 | 206.026 | 139,887 | 0 | 11/0 |

The coding ceiling was 600 seconds for both models. Coder-Next generated a
correct report on the timed-out investigation case, but it is scored as a
failure because completion before the ceiling is part of the frozen rule. Its
large coding token and tool-call totals came primarily from this tool loop.
DeepSeek's specialist latency was substantially higher, so its advantage is a
quality/correctness result rather than a throughput result.

## DeepSeek policy development and lock

The 28-case challenge corpus was run three times under each of four contracts
(336 attempts). Selection maximized completed correctness by suite, then used
fewer non-completions and elapsed time only as tie-breakers.

| Policy | Completed-correct | Elapsed (s) | Tokens | Non-completions |
| --- | ---: | ---: | ---: | ---: |
| structured-v1 | 42/84 | 6,493.3 | 2,155,734 | 0 |
| role-contract | 49/84 | 7,239.1 | 2,187,481 | 1 |
| uncertainty | 50/84 | 7,250.7 | 2,170,947 | 1 |
| verification | 49/84 | 7,605.9 | 2,210,623 | 0 |

Locked routes were reviewer, investigator, and non-coding to uncertainty;
test strategy to structured-v1; and end-to-end workflow to verification.
All workflow policies scored 0/12 in development, making the workflow selection
only a tie-break winner. Confirmation by suite was:

| Suite | DeepSeek | Coder-Next |
| --- | ---: | ---: |
| Reviewer | 18/20 | 14/20 |
| Investigator | 7/8 | 5/8 |
| Test strategy | 2/6 | 4/6 |
| Non-coding | 20/24 | 19/24 |
| Workflow | 0/4 | 0/4 |

## Operational interpretation

- Route bounded coding to DeepSeek when an executable oracle exists; retain
  Coder-Next as a useful fallback rather than the assumed superior coder.
- Prefer DeepSeek as the first-pass reviewer and investigator, but require Codex
  adjudication for security, architecture, compatibility, migrations, and merge
  decisions. It is a stronger reviewer candidate, not an independent authority.
- Keep Coder-Next for test-strategy work until further evidence: it won that
  specialist slice 4/6 to 2/6.
- Do not delegate GitHub state transitions or multi-step orchestration to either
  local model. Both scored 0/4 on workflow confirmation and 3/5 on managed
  lifecycle decisions.
- Use structured issues as the task-fact baseline and role contracts in the
  specialist operation. Prompt evaluation is therefore harness configuration,
  not manual rewriting of every GitHub issue.
- Permit long background execution, but retain explicit ceilings and loop
  telemetry. Correctness selection should not optimize for speed; operational
  scheduling still must account for DeepSeek's roughly 4.5x specialist elapsed
  time in this concurrent confirmation.

The initial concurrent DeepSeek coding run at the old 180-second limit is kept
as infrastructure evidence under `coding-development/deepseek-aborted-180s`.
Canonical raw artifacts and generated scorecards are retained outside git under
`~/.local/share/codex-local-harness/benchmark-165/holistic-v1/`.
