# Local diagnostic dashboard

The dashboard is a visual projection of immutable diagnostic records. It is
not an orchestration surface and deliberately has no buttons or endpoints for
worker launch, configuration, GitHub state, or merge operations.

## Start

```bash
PYTHONPATH=src python3 -m codex_local_harness.dashboard \
  --runs ~/.local/share/codex-local-harness/health/runs.jsonl \
  --adjudications ~/.local/share/codex-local-harness/health/adjudications.jsonl \
  --lifecycle ~/.local/share/codex-local-harness/health/lifecycle.jsonl
```

Open `http://127.0.0.1:8765`. Missing ledger files are treated as empty. The
committed four-state replay in `qualification/v010/` is a better first view
until live runs have adjudications.

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
