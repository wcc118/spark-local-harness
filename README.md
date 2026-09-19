# Codex Local Harness

The installed convergence release is `harness-convergence-2026-09-19`.
Use the [portable installation guide](docs/portable-installation.md) to reproduce
it on another Linux workstation, verify its manifest, and roll back. The release
includes the open implementation stack; `main` alone does not yet reproduce it.

Codex remains the primary orchestrator. Local coding and review have no default
whole-task deadline, retain unpublished work, and report progress through
inference-free supervision. GitHub holds durable project state.

The [comparison report](qualification/convergence-184/REPORT.md) records the
limited smoke test and its limitations. The earlier
[MVP quickstart](docs/mvp-quickstart.md) describes the predecessor setup; use the
portable guide for this release's installation and runtime behavior.

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

### Model Rotation

The harness supports switching between Coder-Next and DeepSeek V4 Flash:

```bash
# Switch to DeepSeek (default for reviewer routes)
codex-local-model use deepseek-v4-reasoning

# Return to Coder-Next (default for coder routes)
codex-local-model use coder-next

# Check active model
codex-local-model status
```

See `docs/deepseek-rotation.md` for full operating behavior and safety features.

After review, `bin/promote-v4-worker` promotes the hardened candidate behind
the stable entry points. It creates a private backup of the qualified baseline
worker first and is safe to rerun after a successful promotion.
The promoted worker imports a content-addressed runtime bundle rather than the
mutable Git checkout. `bin/rollback-v4-worker BACKUP_DIR` restores the prior
worker and runtime pointer.

## Diagnostic dashboard

The `v0.1.0` scaffold adds a localhost-only, read-only diagnostic dashboard.
See [dashboard usage and interpretation](docs/dashboard.md). The four-state
release replay lives in [`qualification/v010/`](qualification/v010/README.md).
