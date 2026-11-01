# Frontier-owned learning during project work

The orchestrator owns this loop as part of normal delivery. Local workers execute
bounded tasks; they do not choose improvements or promote their own results.

1. Retrieve applicable shared lessons and inspect GitHub state. Frontier agents
   may retrieve private oracle knowledge; workers receive shared memory only.
   A memory outage is recorded but does not stop project delivery.
2. Define the engineering objective, acceptance checks and known limitations.
   Use public development checks and independently held evaluation evidence where
   useful. Hidden checks require actual access isolation, not an obscure path.
3. Record an issue assignment before dispatch. For suitable ordinary issues on
   longer runs, alternate Coder-Next implementation/DeepSeek review and DeepSeek
   implementation/Coder-Next review. Keep the assignment through corrections.
   Architecture, security, physics judgment and ambiguous requirements remain
   frontier work; record `exception` with a reason. Exceptions consume no turn.
4. Dispatch sequentially through the existing coder/reviewer entry points and
   model lock. Apply only the selected role's environment object from the record.
   Verify actual model identity in retained run evidence. An assignment records
   intent; it does not load a model, start a worker or bypass admission checks.
5. Inspect actual output and independent evidence. Delivery or APPROVE is not
   acceptance. Capture consequential escapes and repeated friction with exact
   issue/run/commit references. Avoid a memory write for every tool call.
6. Propose a bounded improvement when evidence justifies it. Evaluate against
   frozen checks and valid alternatives; label infrastructure failures separately.
   Retain a frontier promote/reject/hold decision. Never silently weaken a check
   or promote defaults from counters or unlike-issue win rates.
7. During long runs and at handoff, generate a bounded evidence digest. Explain
   remaining uncertainty and the next authorized action. Store reusable public
   lessons in shared memory; private grader contents remain private. A fresh
   session retrieves pointers and resumes from GitHub, not a transcript dump.

## Assignment and evidence commands

Run `python -m codex_local_harness.learning assign --help`. Supply the existing
workers configuration, a durable `assignments.jsonl` path, repository, issue,
task class, decision (`eligible` or `exception`) and reason. Output is JSON.
`role_env.coder` and `role_env.reviewer` each contain the real worker variables
`CODEX_LOCAL_TIER` and `CODEX_LOCAL_MODEL`; do not export both roles together.
The append is locked, synced, and stable on exact retries, including exceptions.
Conflicting inputs fail rather than rewriting history. No secrets belong in it.

`python -m codex_local_harness.learning digest --help` joins assignments to
existing runs, adjudications, lifecycle, workflow and annotation files. Paths
are explicit. Missing files fail visibly; missing values remain null. Repository
identity comes from workflow events, not an issue number alone. Conflicting
repository identities are excluded. The digest never dispatches or promotes.

Use existing strategy annotations for primary-adjudicated `reviewer-catch`,
`reviewer-miss`, `reviewer-false-alarm`, `learning-promote`, `learning-reject`,
and `learning-hold`: source `codex` or `operator`, run_ids, evidence_refs,
summary and observed_at. A model's review alone cannot establish these labels.
Run acceptance and intervention remain in the existing adjudication ledger.
Unknown usage is excluded from token averages with its exclusion count shown.
Partial runtime usage is not a cost estimate or evidence of frontier savings.

## Bounds and rollout

The digest shows at most64 assignments,32 runs per issue and16 attributed
observations per issue. Full durable evidence stays in the existing ledgers.
Assignment records are small and do not contain prompts, tool arguments or logs.
Use the existing worker budgets, exact-head reviews and completion ownership.
No automatic3090 model loads, concurrent large Spark model loads, automatic
route promotion or revival of the retired27B batching experiment is introduced.
The owner-authorized role rotation applies after the upgrade is accepted;
installed defaults remain unchanged for callers without explicit assignments.

Qualification must record what actually ran: a historical replay may demonstrate
capture and rejection, but cannot be presented as prospective role assignment.
A fresh-session retrieval proves continuity, not autonomous model training.
