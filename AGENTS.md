# Codex-Governed Local Harness

Codex is the architectural steward, escalation layer, final semantic reviewer,
and merge authority. Local models perform bounded repository labor. OpenCode is
a replaceable worker runtime, not a top-level orchestrator.

Default coding uses Coder-Next. Use `codex-local-investigator` for read-only
DeepSeek investigation and `codex-local-reviewer` for DeepSeek review. DeepSeek
coding is opt-in with `CODEX_LOCAL_TIER=deepseek codex-local-coder`. The harness
adds the role contract and switches models through its existing worker lock.

## Workflow

1. Work from one GitHub issue with explicit acceptance criteria.
2. Use a dedicated branch and small, reviewable commits.
3. Keep architecture, security boundaries, public API changes, migrations, and
   merge decisions with Codex.
4. Give local workers bounded role packets. They may not select work, redefine
   acceptance criteria, publish approval, merge, or delegate orchestration.
5. Establish facts with deterministic verification before model judgment.
6. Bind every review and publication decision to the exact issue, PR, and head
   SHA. Reject stale evidence.
7. Record normalized run health without credentials or unnecessary raw prompt
   content.
8. Change production behavior only through a measured candidate and preserve a
   tested rollback profile.
9. Own each local worker until the harness exits and its result is consumed.
   Set `CODEX_LOCAL_RECORD_RUN=1` and preserve the calling Codex session identity.
   Retain the execution session ID and poll with `write_stdin` until exit; resume
   yielded `functions.exec` cells with `functions.wait` first. Keep waits bounded
   to 30 seconds and do not end the turn while a worker is pending. Inspect exit,
   result, and GitHub state; acknowledge the run with `codex-local-completion ack`
   and record the next action. If the handle is lost, use
   `codex-local-completion status --wait 30`. Never treat quiet inference as
   completion or a terminal failure as success. See `docs/worker-completion.md`.

## Merge policy

Codex may merge when acceptance criteria are satisfied, required checks pass,
the reviewed head is current, the diff is within scope, evidence is complete,
and no unresolved material finding remains. A local model verdict alone never
authorizes merge.

Codex must block or request correction for ambiguous requirements, stale or
contradictory evidence, weakened tests, unexplained failures, unexpected risk,
or a broken harness invariant.

## Recursive improvement

The harness learns through controlled experiments:

1. Observe normalized health evidence.
2. Diagnose a repeated behavior.
3. Record a falsifiable hypothesis in an issue or ADR.
4. Change one bounded variable.
5. Compare the candidate with a frozen baseline.
6. Promote or roll back through a Codex merge decision.

Telemetry never changes prompts, routes, permissions, or merge policy
automatically.

## Opt-in convergence candidate (#181)

`bin/codex-local-worker-candidate` is an isolated qualification entry point; do not
install it over production without a separate promotion decision. See
`docs/local-convergence.md` for its supervision, guidance and accounting commands.
For this candidate, bounded means issue scope and acceptance, not an automatic
whole-task timer or fixed correction count. Codex assesses evidence of progress,
supplies missing details and resolves genuine stalls. Mechanical polling continues
without model inference; read incremental evidence and avoid replaying full logs.
Keep packets focused on current criteria, unresolved findings and relevant evidence.
Inspect and acknowledge each terminal execution before checkpoint continuation.
Record meaningful outcomes and next actions in GitHub so a new session can resume.
