# Local diagnostic dashboard

The dashboard is a visual projection of immutable diagnostic records. It is
not an orchestration surface and deliberately has no buttons or endpoints for
worker launch, configuration, GitHub state, or merge operations.

## Operational verdict and attention queue

At the top of the dashboard, a deterministic **operational verdict** provides
an immediate summary of the harness's current state. This read-only projection
is derived solely from existing immutable records and does not issue
recommendations or launch actions.

### Severity levels

| Severity | Condition |
|----------|-----------|
| `CRITICAL` | Contradictions, interrupted/cancelled/timed-out runs, or failed/blocked workflow operations |
| `DEGRADED` | Unadjudicated runs, publication failures, missing/malformed sources, or telemetry older than 30 minutes |
| `NO-DATA` | No diagnostic or workflow records have been written; source problems remain visible without fabricating an operational conclusion |
| `HEALTHY` | All systems nominal |

### Attention queue

Below the verdict, an **attention queue** displays the most urgent items
requiring operator review:

- Items are ordered by severity and then actual observed time, newest first;
  arbitrary run IDs are never interpreted as timestamps
- The initial view shows the 5 most urgent items; additional items are hidden
  with a count calculated before the queue is bounded
- Each item includes the subject, reason, and a GitHub issue link when
  repository identity is available
- GitHub issue links open in a new tab with `noopener noreferrer` for security

### What each category means

- **Contradictions**: A run claimed success but the independent evaluator
  observed failure, or vice versa
- **Interrupted/cancelled/timed-out**: Operations that did not complete
  successfully due to external factors
- **Telemetry publication failures**: Failed attempts to publish runtime metrics
- **Missing/malformed source files**: Diagnostic ledgers that could not be read
- **Stale sources**: Readable telemetry whose last filesystem update is more
  than 30 minutes old; a live SSE connection does not imply fresh evidence
- **Unadjudicated runs**: Execution exists but independent judgment has not
  been observed; this is uncertainty, not inferred failure

A run with both a claim contradiction and a terminal interruption appears as
one incident with both reasons. Session filtering includes only publication
failures bound to that session's runs. Global source health remains visible
because it affects the trustworthiness of every session projection.

The queue is diagnostic only; it does not automatically resolve issues or
trigger workers. Use the GitHub issue links to navigate to the relevant work
items for investigation.

## Start

```bash
PYTHONPATH=src python3 -m codex_local_harness.dashboard \
  --runs ~/.local/share/codex-local-harness/health/runs.jsonl \
  --adjudications ~/.local/share/codex-local-harness/health/adjudications.jsonl \
  --lifecycle ~/.local/share/codex-local-harness/health/lifecycle.jsonl \
  --workflow ~/.local/share/codex-local-harness/health/workflow.jsonl \
  --frontier ~/.local/share/codex-local-harness/health/frontier.jsonl \
  --publication-failures ~/.local/share/codex-local-harness/health/publication-failures.jsonl \
  --operation-usage ~/.local/share/codex-local-harness/health/operation-usage.jsonl \
  --plans ~/.local/share/codex-local-harness/health/plans.jsonl \
  --mode live
```

Open `http://127.0.0.1:8765`. Missing ledger files are treated as empty. The
committed four-state replay in `qualification/v010/` is a better first view
until live runs have adjudications.

The browser opens one Server-Sent Events stream. Ledger changes are detected
server-side and pushed immediately; the browser does not poll. The connection
indicator changes to `live` or `disconnected`, and the newest source timestamp
shows whether displayed data is fresh.

## What to look for

- **Run history defaults to triage.** The table opens on "Needs judgment · N",
  the same bucket used to compute `unadjudicated_runs`. Switch to
  "Contradictions" or "All" with the tabs above the table; a text filter and
  click-to-sort column headers work within whichever tab is active. This is
  all client-side filtering over the already-fetched read-only snapshot — no
  new endpoint, no mutation.
- **The session picker** narrows every section below it to one session.
  Options are labeled `repository#issue` (or the operation ID, or "Legacy
  (no session ID)" for events recorded before session tracking existed) plus
  a status and the operation count, not the bare session UUID. Picking a
  session submits the form via a real `change` listener in `dashboard.js` —
  earlier releases used an inline `onchange` attribute, which the page's own
  `script-src 'self'` CSP silently blocks in a standards-compliant browser,
  so the selector looked interactive but never actually changed the page.
- **The freshness chip** next to the connection indicator reports the age of
  the newest source file, independent of the SSE socket state: green under 5
  minutes, amber under 30, red beyond that, grey when there is no data yet.
  A live socket does not mean fresh data — check both.
- **Claim** is what worker execution reported.
- **Observed** is the latest independent evaluator, Codex, operator, or
  reviewer adjudication.
- **Contradiction** means those disagree; neither record is erased.
- **Needs judgment** means a run has not been independently adjudicated.
- **Lifecycle** makes cancellations, interruptions, and timeouts visible even
  when normal worker telemetry was never finalized.
- **Evidence references** are bounded identifiers into durable evidence, not
  captured prompt or response text.
- **Current workflow** shows durable delivery milestones, collapsed by default
  into a dot rail (one glance, colored by state) with the current milestone
  named beside it. Click the rail to expand the full milestone grid. Evidence
  is attached as activity, correction is a numbered loop to implementation,
  and missing observations remain `pending` rather than being inferred as
  passed.
- **A malformed or missing source** gets its own chip in the warning banner
  with a one-line cause (e.g. "unreadable — check the last line for
  truncated or invalid JSON"), not just a bare filename.
- **Telemetry publication failures** expand in place under the summary count
  to the sanitized failure list already written to
  `publication-failures.jsonl`, rather than leaving the count with nowhere
  to go.
- **Scroll position and any open evidence/history panels survive a live
  refresh.** The SSE-triggered reload still swaps the whole page, but the
  client remembers what was open and where you were before replacing the
  DOM, then restores both.
- **Running plan** shows the latest concise public plan revision beside its
  matching workflow. It includes objective, ordered step states, progress,
  evidence references, and revision history. It never stores or displays
  private reasoning or chain-of-thought. Missing, stale, malformed, conflicting,
  and superseded plan state remains explicit.
- **Recorded frontier tokens** include only frontier usage present in harness
  run records. They do not estimate this interactive Codex thread.
- **Local tokens** include finalized normalized worker runs. The total updates
  when a run finishes; it does not estimate tokens during active inference.
  The "Processed context" card carries a small sparkline of local tokens
  across the most recent runs so a volume shift is visible at a glance.
- **Cached input** is not a first-class card — most local worker profiles
  don't report it yet, so it lives as a footnote under Division of Labor
  instead of occupying a card slot that would otherwise always read "Not
  reported (unsupported)."
- **Interactive Codex tokens** are separate and stay `Unavailable` unless an
  authoritative measured or timestamped operator-reported value is supplied.
- **Codex context and plan** show measured or timestamped operator-reported
  values. They remain `Unavailable` when no authoritative interface exists.
- **"Frontier" in Division of Labor is assistant-neutral**, not Codex-only.
  Both `codex_local_harness.operation_usage` (parses Codex's own
  `rollout-*.jsonl` session log, a running total, diffed across a start/finish
  pair) and `codex_local_harness.claude_usage` (sums Claude Code's per-turn
  `message.usage` from its own `~/.claude/projects/<project>/<session>.jsonl`
  transcript, tagged `source: claude-transcript-usage`) append into the same
  `operation-usage.jsonl` ledger, so any interactive assistant that
  self-reports at an operation's start and finish shows up here. Claude has
  no scriptable context-window interface, so `claude_usage` requires
  `--context-window` as an explicit, vendor-published value rather than
  inferring one.

Counts are descriptive. Small samples are not evidence of architectural
improvement, and the dashboard intentionally does not recommend or apply
changes.

### Accessibility

State is conveyed by visual indicators (color) **and** explicit labels:
- Severity chips always include the severity level text (e.g., "CRITICAL")
- Attention items include both severity label and reason text
- No information is conveyed by color alone
- The attention queue is fully accessible via keyboard navigation and screen
  readers using proper ARIA semantics

## Safety boundary

- Server binding is restricted to loopback addresses.
- Only `GET` is accepted; all mutation methods return `405`.
- Responses are `no-store` and use a restrictive content-security policy.
- Derived text is HTML escaped.
- Reading a snapshot does not alter any source ledger.
- There is no authentication because there is no non-loopback binding. A
  future remote deployment would require a separate threat model and is not
  part of `v0.1.0`.

Telemetry publication is best-effort and cannot change a worker's outcome.
Sanitized failures are retained in `publication-failures.jsonl` and displayed
as a dashboard warning. Exact replay is idempotent; conflicting reuse of a run
or event identity is rejected.
