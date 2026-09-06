# Milestone-based context compaction

Issue 169 treats durable GitHub state and explicit checkpoint records as project
memory. Model-visible conversation history is an execution cache, not the source
of truth.

## Boundaries

- Primary Codex control is permitted only for threads created by the harness on
  its private stdio app-server transport. The controller never discovers or
  attaches to an existing socket. An observed external thread is advisory-only.
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
`last_token_usage.input_tokens / model_context_window` in Codex rollouts (and
the equivalent app-server `last.inputTokens` counter); unavailable inputs stay unavailable.
Compaction records contain identifiers, scalar counters, lifecycle state, and
error class only. Local OpenCode and primary Codex use different surfaces.

The local auto-only contract is pinned to OpenCode 1.18.21 and is verified from
the server health/config endpoints before compaction telemetry is accepted. Each
disposable worker config explicitly sets `auto=true`, `prune=true`, and
`reserved=16000`. `session.next.compaction.delta` and the `text`/`include`
members on completion are ignored at the privacy boundary. Manual local
compaction has no enabled call path.

Qualification compares a default-off candidate with a frozen auto-only
baseline. Net input includes the compaction pass and bounded resume packet.
Cached and uncached input, output, reasoning, tool loops, wall time, state
retention, and task outcome remain separate measures. Gross replay estimates do
not authorize promotion.

The first candidate threshold is 0.62 of the authoritative model window, with a
two-turn cooldown. Promotion requires a predeclared material net-input
improvement, no increase in uncached input, no state-retention failures, no
task-quality regression, and a tested rollback profile.

`profiles/context-compaction-auto-only.freeze.json` is the rollback profile.
`profiles/context-compaction-candidate.json` remains disabled. The first
measured shared-prefix paired replay retained state and task outcome with equal
tool loops, but improved net input by only 1.329% and increased uncached input
by 99,575 tokens after including the compaction pass and resume request. That
fails both promotion gates. Privacy-minimal evidence is retained in issue 169;
a later candidate must be requalified against the frozen baseline. The
installed release boundary is not changed by this candidate.

The historical `bin/codex-local-worker` baseline remains byte-for-byte frozen;
the explicit disposable configuration is applied in the maintained v4 worker.
