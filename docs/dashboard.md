# Local diagnostic dashboard

The dashboard is a visual projection of immutable diagnostic records. It is
not an orchestration surface and deliberately has no buttons or endpoints for
worker launch, configuration, GitHub state, or merge operations.

## Start

```bash
PYTHONPATH=src python3 -m codex_local_harness.dashboard \
  --runs ~/.local/share/codex-local-harness/health/runs.jsonl \
  --adjudications ~/.local/share/codex-local-harness/health/adjudications.jsonl \
  --lifecycle ~/.local/share/codex-local-harness/health/lifecycle.jsonl \
  --workflow ~/.local/share/codex-local-harness/health/workflow.jsonl \
  --frontier ~/.local/share/codex-local-harness/health/frontier.jsonl \
  --publication-failures ~/.local/share/codex-local-harness/health/publication-failures.jsonl \
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

- **Claim** is what worker execution reported.
- **Observed** is the latest independent evaluator, Codex, operator, or
  reviewer adjudication.
- **Contradiction** means those disagree; neither record is erased.
- **Needs judgment** means a run has not been independently adjudicated.
- **Lifecycle** makes cancellations, interruptions, and timeouts visible even
  when normal worker telemetry was never finalized.
- **Evidence references** are bounded identifiers into durable evidence, not
  captured prompt or response text.
- **Current workflow** shows the latest state of intake, routing, local work,
  delivery, review, evidence, Codex decision, correction, merge, and post-merge.
  Missing observations remain `pending`; stages explicitly recorded as
  `not-applicable` display as `not needed`.
- **Recorded frontier tokens** include only frontier usage present in harness
  run records. They do not estimate this interactive Codex thread.
- **Local tokens** include finalized normalized worker runs. The total updates
  when a run finishes; it does not estimate tokens during active inference.
- **Interactive Codex tokens** are separate and stay `Unavailable` unless an
  authoritative measured or timestamped operator-reported value is supplied.
- **Codex context and plan** show measured or timestamped operator-reported
  values. They remain `Unavailable` when no authoritative interface exists.

Counts are descriptive. Small samples are not evidence of architectural
improvement, and the dashboard intentionally does not recommend or apply
changes.

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
