# Milestone-based context compaction

Issue 169 treats durable GitHub state and explicit checkpoint records as project
memory. Model-visible conversation history is an execution cache, not the source
of truth.

## Boundaries

- Primary Codex control is permitted only for threads created by the harness on
  its private app-server transport. An observed external thread is advisory-only.
- `thread/compact/start` is an acknowledgement, not completion. Completion is
  the matching `contextCompaction` item lifecycle and a successful enclosing
  turn. The first later ordinary token-usage event supplies the after size.
- One compaction may be in flight for a thread. Disconnect recovery reconciles
  observed state and never retries an ambiguous request automatically.
- Compaction is eligible only when the candidate is enabled, the thread is idle,
  no tool is active, a nonterminal milestone is complete, more work remains, the
  checkpoint is causally current, and model-relative pressure crosses policy.

## Durable checkpoint

`ContextCheckpointEvent` is separate from `PlanEvent`. It binds the exact
repository, issue, optional PR, head object id, session, latest completed turn,
plan event and revision, acceptance criteria, decisions, test evidence,
blockers, relevant files, and next action. References point to public durable
evidence; raw prompts, reasoning, tool output, credentials, and compaction
summaries are forbidden.

A checkpoint becomes stale after any subsequent turn, plan revision, relevant
GitHub identity change, or head change. Wall-clock age alone never establishes
freshness. The bounded canonical JSON packet is suitable for starting a fresh
issue-scoped thread and has a stable SHA-256 digest.

## Measurement

Capture cumulative and last-request counters separately. Context pressure is
`last.input_tokens / model_context_window`; unavailable inputs stay unavailable.
Compaction records contain identifiers, scalar counters, lifecycle state, and
error class only. Local OpenCode and primary Codex use different surfaces.

Qualification compares a default-off candidate with a frozen auto-only
baseline. Net input includes the compaction pass and bounded resume packet.
Cached and uncached input, output, reasoning, tool loops, wall time, state
retention, and task outcome remain separate measures. Gross replay estimates do
not authorize promotion.

The first candidate threshold is 0.62 of the authoritative model window, with a
two-turn cooldown. Promotion requires a predeclared material net-input
improvement, no state-retention failures, no task-quality regression, and a
tested rollback profile.
