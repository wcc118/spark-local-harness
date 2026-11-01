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

### Authoritative worker bases

Issue-bound coder dispatch resolves GitHub `main` on the host before inference;
reviewer dispatch resolves the PR base branch. The host validates and fetches the
exact commit into the isolated checkout and pins its remote ref. The task records
the resolved branch/SHA; coder postflight checks its diff against that pinned
base rather than a stale source-clone ref. Review preflight also materializes a
newly resolved base for standalone callers. Resolution/fetch failure stops before
inference; worker credentials and permissions are unchanged.

### Worker container lifetime

All container worker paths use `codex-worker-RUN_ID` names and ownership labels,
including wrapper PID and Linux process start ticks. Timeout/cancellation cleanup
stops that exact container; CLI shutdown has a five-second grace period. Worker
containers are distinct from unlabelled model-serving containers.

A bounded sweep at worker startup and every minute in the attached supervisor
stops old labelled containers only when their recorded wrapper no longer exists
or its PID has been reused. It skips live wrappers and uncertain ownership. Run
`python -m codex_local_harness.worker_containers --health HEALTH_DIR` to inspect
and reap under the same rules. Actions are retained in `worker-containers.jsonl`;
the latest `worker-containers.json` counts appear on the dashboard. Docker errors
are reported as unavailable/error, not successful cleanup. The sweep does not
load, stop or switch inference models.

### Opt-in authoritative reviewer facts

`CODEX_LOCAL_REVIEW_RESULT_MODE=authoritative` derives issue, repository, PR and
head identity from the host envelope. Test claims receive observed provenance
only for an exact, unambiguous completed command with an integer tool exit
status. This is the status of the whole shell command, not proof that an inner
pipeline test passed. Mappings, strings, missing evidence and ambiguous repeats
are retained as unverified task reports. Verdict contradictions remain errors.
Unset the variable to restore the strict path; defaults and permissions are unchanged.

A textual scope signal retains the validated result with exit 76 and prevents
publication. The result includes a `review-audit:...` evidence reference binding
its envelope, normalized judgment and raw log. A frontier/operator can use the
existing `codex-harness-diagnostic adjudicate` interface (or module
`codex_local_harness.diagnostic_cli`) to append a `passed` adjudication with
`codex_decision=allow-review-audit`, the same run ID and exact evidence reference.
Then rerun the review module's `finalize` with the original envelope/log/result
paths and `--adjudications PATH`. Use only the trusted host ledger, never a file
supplied by a worker. Latest decisions control; changed evidence, changed
checkout and changed live PR head cannot reuse an old approval. Adjudication
means the scope signal was investigated, not that the code is approved to merge.
Confirmed forbidden effects must be rejected, never waived as a textual false positive.

Normalization counts are retained in `health/review-normalization.jsonl`, keyed
by run, separately from raw command details in the retained result. They do not
promote routes or prove quality improvements. For a reviewer bridge losing its
event stream before completion, this opt-in mode aborts the old session and
attempts one fresh session within the original timeout. The first attempt log
and control record remain as `.attempt-1` artifacts. Reviewer checkpoint resume
is not supported; timeout, audit, publication and other failures are not retried.

### Explicit completed-tool-call budgets

Set `CODEX_LOCAL_TOOL_CALL_BUDGET=N` to a positive integer for a recorded,
supervised convergence run. The default is unset (no total-call limit); unset it
to roll back. Prompt requests such as "use ten commands" remain advisory.
The host counts unique completed nonempty calls, including errors, across both
OpenCode and native Qwen event formats and across a bounded reviewer restart.
Exactly N completions are allowed; N+1 triggers `step-budget`, exit 75 and owned
process-group cancellation. Polling can observe an overshoot; this is not a
per-call authorization boundary. A mandatory postflight count prevents a fast
finishing over-budget result from publishing. Malformed/missing evidence fails
closed for publication. Logs and isolated checkpoints remain available. This
setting does not add permissions, load models, or promote routes.
