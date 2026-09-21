# LM Studio Lane Health Check and GPU Lease

## Overview

The lane system provides health checks and resource leasing for LM Studio instances that serve models for advisory jobs. It ensures:

1. **Health verification** - The remote LM Studio instance is reachable and has the expected model loaded with sufficient context
2. **Exclusive GPU access** - Lease mechanism prevents concurrent GPU jobs from overlapping
3. **Fail-open semantics** - Health failures skip jobs gracefully without failing the entire run

## Components

### `src/codex_local_harness/lane.py`

Core library module with:

- **`LaneStatus`** dataclass: Health check result with fields `status`, `model_id`, `loaded_context`, `quantization`, `latency_seconds`, `checked_at`, `detail`
- **`check_lane()`** function: Performs HTTP health check against LM Studio API
- **`GpuLease`** class: Advisory lock using `fcntl.flock` with sidecar metadata

### `src/codex_local_harness/lane_cli.py`

CLI interface with subcommands:

- `health`: Check lane health
- `lease-status`: Show current holder
- `run`: Execute command while holding lease
- `guard`: Execute command only if lane is healthy

### `bin/codex-local-lane-health`

Bash wrapper script that sets `PYTHONPATH` and invokes the CLI.

## Usage

### Health Check

```bash
# Basic health check (human-readable)
codex-local-lane-health health --model qwen/qwen3.8-27b

# With custom URL
codex-local-lane-health health --base-url http://10.0.0.92:1234/v1 --model qwen/qwen3.8-27b

# JSON output for programmatic use
codex-local-lane-health health --model qwen/qwen3.8-27b --json

# With authentication token
export LMSTUDIO_API_TOKEN="your-token"
codex-local-lane-health health --model qwen/qwen3.8-27b
```

### Lease Management

```bash
# Check current lease holder
codex-local-lane-health lease-status --name gpu-advisory

# Run command with lease (acquires, runs, releases)
codex-local-lane-health run --name gpu-advisory --purpose "advisory job" -- 60s command ...

# Guard command with health check
codex-local-lane-health guard --model qwen/qwen3.8-27b -- min-context 65536 -- command ...

# Strict mode: fail if lane unhealthy
codex-local-lane-health guard --model qwen/qwen3.8-27b --strict -- command ...
```

### Exit Codes

| Code | Status | Meaning |
|------|--------|---------|
| 0 | ok | Lane healthy, command executed |
| 10 | unreachable | LM Studio not reachable |
| 11 | model-not-loaded | Expected model not loaded |
| 12 | context-too-small | Context below minimum requirement |
| 13 | error | Other error (HTTP error, malformed JSON, etc.) |
| 75 | lease-failed | Failed to acquire lease within timeout |

### Fail-Open Semantics

The `guard` command implements fail-open behavior:

- **Without `--strict`**: If health check fails, prints `LANE-SKIPPED: STATUS DETAIL` to stderr and exits 0
- **With `--strict`**: Propagates the health check exit code

This design ensures:
- Jobs don't fail the entire run due to LM Studio unavailability
- Operators are notified via stderr when jobs are skipped
- Strict mode available for critical jobs that must have the model

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `LMSTUDIO_BASE_URL` | `http://127.0.0.1:1234/v1` | LM Studio API base URL |
| `LMSTUDIO_API_TOKEN` | (none) | Optional Bearer token for authentication |

### LM Studio PC Checklist

For reliable LM Studio serving of `qwen/qwen3.8-27b`:

- [ ] **Auto-unload**: Disabled (or TTL set very high)
- [ ] **Windows sleep**: Disabled (keep system awake)
- [ ] **Model loaded**: `qwen/qwen3.8-27b` actively loaded
- [ ] **Context length**: Set to 65536 (or higher)
- [ ] **Quantization**: `Q8_0` for balance of speed/quality
- [ ] **Flash attention**: Enabled
- [ ] **MTP speculative decoding**: Enabled with 3 draft tokens
- [ ] **Max concurrent predictions**: Set to 1 (prevents GPU saturation)

## Lock File Details

### Lock Files

- **Lock file**: `~/.local/share/codex-local-harness/lane-NAME.lock`
- **Sidecar file**: `~/.local/share/codex-local-harness/lane-NAME.sidecar.json`

### Sidecar Format

```json
{
  "pid": 12345,
  "purpose": "job description",
  "started_at": "2025-09-21T10:30:00+00:00"
}
```

### Staleness Detection

The system automatically detects and clears stale sidecar data when the holder process no longer exists (using `os.kill(pid, 0)`).

## Examples

### Job Script with Lane Guard

```bash
#!/usr/bin/env bash
set -euo pipefail

# Guard: check LM Studio is healthy before running
codex-local-lane-health guard \
  --base-url http://10.0.0.92:1234/v1 \
  --model qwen/qwen3.8-27b \
  --min-context 65536 \
  -- \
  python3 run_advisory_job.py

echo "Job completed successfully"
```

### Concurrent Job Protection

```bash
#!/usr/bin/env bash
set -euo pipefail

# Only one advisory job at a time
codex-local-lane-health run \
  --name gpu-advisory \
  --purpose "advisory job $$" \
  --lease-timeout 30 \
  -- \
  python3 run_advisory_job.py
```

### Programmatic Health Check

```python
from codex_local_harness.lane import check_lane

result = check_lane(
    base_url="http://10.0.0.92:1234/v1",
    expected_model="qwen/qwen3.8-27b",
    min_context=65536,
)

if result.status != "ok":
    print(f"Skipping job: {result.detail}")
    exit(0)  # Graceful skip, not failure
```

## Testing

Run tests with:

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_lane.py
```

Tests cover:

- Health check for all status types (ok, unreachable, model-not-loaded, context-too-small, error)
- Token authentication
- Lease acquire, release, and context manager
- Contention between processes
- Stale sidecar detection
- CLI subcommands and exit codes
- Fail-open vs strict modes
