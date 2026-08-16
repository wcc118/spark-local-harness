# MVP Quickstart

This quickstart covers setting up and using the Codex Local Harness in a new repository.

## Prerequisites

- Python 3.11 or later
- systemd user session support (Linux)
- opencode installed and configured
- Docker available to the invoking user

Build the confined worker image once after cloning, and again whenever
`docker/worker/Dockerfile` changes:

```bash
docker build -t codex-local-worker:0.1.0 docker/worker
```

Local workers run in this container by default. Only the selected checkout and
a disposable OpenCode home are mounted; helper and reviewer checkouts are
read-only. The emergency `CODEX_LOCAL_SANDBOX=disabled` path also requires the
explicit `CODEX_LOCAL_ALLOW_UNSANDBOXED=1` acknowledgement and is not a
qualified production route.

## Installation

The harness is installed as a Python package. In your project:

```bash
pip install -e .
```

The worker entrypoint is `codex-local-worker`. Reviewer invocations are documented in the Reviewer transaction section.

## Routing Guidance

The harness uses two distinct worker routes:

| Route | Model | Purpose | Configuration |
|-------|-------|---------|---------------|
| **helper/coder** | `lmstudio/qwen/qwen3.8-27b` | Standard-tier tasks | `profiles/standard.json` |
| **complex/coder/reviewer** | `vllm/Qwen/Qwen3-Coder-Next-FP8` | Complex tasks requiring review | `profiles/complex.json` |

Task delegation between routes is disabled by default in installed configuration for safety. See routing constraints below.

## Escalation Boundaries

### What the harness does
- Enforces deterministic boundaries around local OpenCode workers
- Routes tasks to appropriate worker models
- Collects diagnostics, workflow, and evidence records
- Provides a read-only diagnostic dashboard

### What the harness does NOT do
- **No orchestration**: The harness is not an workflow controller. Codex remains the orchestrator.
- **No automatic escalation**: Routing to complex models is explicit via profile configuration, not automatic escalation.
- **No merge authority**: The harness records delivery and workflow state but does not authorize merges. Codex adjudication is final.

### Review/merge contract
1. Local work completes and produces deliverables
2. Reviewer evaluates against acceptance criteria
3. Evidence packet records the review outcome
4. **Codex adjudication is required** for merge authorization
5. Deterministic postflight validates the complete workflow

The current reviewer implementation has a known limitation with tempfile detection. See known limitations below.

## Dashboard

The harness includes a read-only diagnostic dashboard accessible at:

```
http://127.0.0.1:8765
```

### Starting the dashboard

```bash
PYTHONPATH=src python3 -m codex_local_harness.dashboard \
  --runs ~/.local/share/codex-local-harness/health/runs.jsonl \
  --adjudications ~/.local/share/codex-local-harness/health/adjudications.jsonl \
  --lifecycle ~/.local/share/codex-local-harness/health/lifecycle.jsonl \
  --workflow ~/.local/share/codex-local-harness/health/workflow.jsonl \
  --frontier ~/.local/share/codex-local-harness/health/frontier.jsonl \
  --publication-failures ~/.local/share/codex-local-harness/health/publication-failures.jsonl \
  --operation-usage ~/.local/share/codex-local-harness/health/operation-usage.jsonl \
  --mode live
```

### Health check endpoint

```
GET http://127.0.0.1:8765/healthz
```

Response: `{"status":"ok","read_only":true}`

### Safety features
- **Loopback-only**: Binds to `127.0.0.1` by default; cannot be configured for external binding
- **GET-only**: All non-GET methods return `405 Method Not Allowed`
- **No-store**: Responses are never cached
- **Content-Security-Policy**: restrictive CSP (`default-src 'none'`)
- **HTML escaping**: All derived text is escaped

### What to monitor
- **Adjudicated/Unadjudicated**: Track which local runs have independent evaluation
- **Contradictions**: Disagreements between worker claims and observed outcomes
- **Local tokens**: Total token usage from finalized worker runs
- **Frontier tokens**: Token usage for frontier evaluation
- **Source age**: Time since last source file update (live mode indicator)

## Rollback Reference

### Worker rollback

The stable worker installation is protected by a private backup机制:

```bash
# Promote a candidate (creates backup automatically)
bin/promote-v4-worker

# Restore a prior worker from backup
bin/rollback-v4-worker /path/to/backup
```

### Health data backup

Health data is stored in `~/.local/share/codex-local-harness/health/`. Backups follow this naming convention:

```
~/.local/share/codex-local-harness/backups/YYYYMMDDTHHMMSSZ-$(DESCRIPTION)
```

## Known Limitations

### Reviewer tempfile detection
The reviewer has a known limitation where it may incorrectly approve unmanaged `tempfile.mkdtemp()` usage despite explicit evidence and rubric experiments indicating this should be flagged. This remains an open issue pending a more robust detection strategy.

### Routing constraints
- Task delegation between routes is explicitly denied in installed configuration
- Complex model routing requires explicit profile configuration, not automatic escalation
- Reviewer is non-authoritative; deterministic postflight and Codex adjudication are always required

### Deferred work
- Issue #55: Dashboard workflow redesign
- Issue #56: Workflow schema optimization

## Example: Quick Test in a New Repository

```bash
# 1. Clone and install
git clone <your-repo>
cd <your-repo>
pip install -e .

# 2. Configure profiles (see profiles/ directory)

# 3. Start the diagnostic dashboard
PYTHONPATH=src python3 -m codex_local_harness.dashboard \
  --runs ~/.local/share/codex-local-harness/health/runs.jsonl \
  --adjudications ~/.local/share/codex-local-harness/health/adjudications.jsonl \
  --lifecycle ~/.local/share/codex-local-harness/health/lifecycle.jsonl \
  --mode live

# 4. Open http://127.0.0.1:8765 in your browser

# 5. Run tests to verify installation
PYTHONPATH=src python3 -m pytest -q
```

## Systemd Deployment (Optional)

For production deployments, template unit files are provided in the `deployment/` directory:

- `dashboard-backend.service` — Loopback-only dashboard backend
- `socat-lan-proxy.service` — Optional LAN proxy (requires operator review of bind address)

See `deployment/README.md` for installation instructions.
