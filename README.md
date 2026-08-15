# Codex Local Harness

The project is building toward the first diagnostic scaffold release,
`v0.1.0`. Codex remains the primary orchestrator. Local workers perform one
bounded task. See [the tracked release plan](docs/v0.1.0-plan.md).

## Reviewer transaction

1. Resolve and verify the canonical checkout, GitHub remote, issue, PR, branch,
   head SHA, base SHA, clean state, and workflow label.
2. Inject that identity as an immutable envelope.
3. Run the local reviewer without durable-state ownership.
4. Audit its raw tool log for prohibited commands and paths outside the
   checkout, and verify that the checkout did not change.
5. Parse and validate exactly one versioned JSON verdict.
6. Reject stale verdicts if the PR head changed.
7. Publish an idempotently marked PR comment and deterministic label change.
8. Re-read GitHub state; only then record workflow success.

Because the current GitHub CLI identity is also the PR author, the publisher
uses a clearly marked PR comment rather than claiming an independent formal
GitHub approval.

## Running tests

```bash
PYTHONPATH=src python3 -m pytest -q
```

## Installed boundary

The qualified worker entrypoint is installed at
`~/.local/bin/codex-local-worker`. Reviewer invocations require:

```bash
CODEX_LOCAL_REPOSITORY=OWNER/REPO \
CODEX_LOCAL_ISSUE=N \
CODEX_LOCAL_PR=N \
CODEX_LOCAL_RECORD_RUN=1 \
codex-local-reviewer CHECKOUT --task-file PACKET.md
```

`legacy-v1/` contains rollback artifacts. Experimental routing described by
`profiles/candidate-v3.json` is not installed and must not be compared until
the corrected baseline qualification suite remains green.

## Diagnostic dashboard

The `v0.1.0` scaffold adds a localhost-only, read-only diagnostic dashboard.
See [dashboard usage and interpretation](docs/dashboard.md). The four-state
release replay lives in [`qualification/v010/`](qualification/v010/README.md).
