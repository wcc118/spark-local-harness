# Session and Token Capture

This document describes the session identity, token usage capture, and metric provenance for the Codex Local Harness.

## Overview

The harness tracks work through **sessions** that represent distinct operational epochs. Each session:

- Is identified by a unique `session_id` (derived from `CODEX_THREAD_ID` when available)
-Is associated with a GitHub issue (and optionally a pull request)
- Maintains an operation-usage model with privacy-minimal token tracking
- Supports explicit start/finish boundaries for accurate delta calculation

## Command Reference

### Session Identity

```bash
# Record a session identity
codex-local-session identity record \
    --records ~/.local/share/codex-local-harness/health/identities.jsonl \
    --issue 62 \
    --session-id "thread_abc123" \
    --repository "OWNER/REPO" \
    --pull-request 123 \
    --environment "production"

# Look up session identity from environment
codex-local-session identity lookup \
    --issue 62

# Aggregate session data
codex-local-session identity aggregate \
    --identity-records identities.jsonl \
    --usage-records sessions.jsonl \
    --output summaries.jsonl
```

### Session Usage

```bash
# Record a token usage snapshot
codex-local-session usage record \
    --records ~/.local/share/codex-local-harness/health/sessions.jsonl \
    --session-id "thread_abc123" \
    --issue 62 \
    --start-tokens 1000 \
    --finish-tokens 2500 \
    --cache-read 50 \
    --cache-write 25 \
    --explicit-provenance "thread-id-file"

# Parse usage from a rolling file
codex-local-session usage from-file \
    --records ~/.local/share/codex-local-harness/health/sessions.jsonl \
    --file /path/to/thread-id-rollout \
    --issue 62

# Record an interrupted session (explicit state)
codex-local-session usage record \
    --records sessions.jsonl \
    --session-id "thread_xyz" \
    --issue 62 \
    --interrupted
```

### Token Snapshots

```bash
# Capture token snapshot from CODEX_THREAD_ID file
codex-local-session token record \
    --records ~/.local/share/codex-local-harness/health/sessions.jsonl \
    --thread-id-file ~/.local/share/codex-local-harness/health/thread-id-rollout \
    --session-id "thread_abc123" \
    --issue 62 \
    --repository "OWNER/REPO"

# Calculate delta between start and finish snapshots
codex-local-session token delta \
    --start-record start_tokens.jsonl \
    --finish-record finish_tokens.jsonl \
    --output-format json
```

### Explicit Boundaries

```bash
# Record start boundary
codex-local-session boundary start \
    --records sessions.jsonl \
    --session-id "thread_abc123" \
    --issue 62 \
    --tokens 1000

# Record finish boundary
codex-local-session boundary finish \
    --records sessions.jsonl \
    --session-id "thread_abc123" \
    --issue 62 \
    --start-record start_snapshot.jsonl \
    --tokens 2500
```

## Metric Definitions

### Token Counts

| Metric | Type | Description | Source |
|--------|------|-------------|--------|
| `input_tokens` | Integer | Number of input tokens consumed | OpenCode step_finish events |
| `output_tokens` | Integer | Number of output tokens generated | OpenCode step_finish events |
| `reasoning_tokens` | Integer | Number of reasoning tokens used | OpenCode step_finish events |
| `cache_read` | Integer | Tokens served from cache | Explicit provenance |
| `cache_write` | Integer | Tokens written to cache | Explicit provenance |

### Session States

| State | Description | Delta Handling |
|-------|-------------|----------------|
| `normal` | Standard session with complete data | `delta = finish - start` |
| `interrupted` | Session ended prematurely | `delta = null` (no data) |
| `missing` | No data available | `delta = null` |
| `reset` | Token count reset to zero | `delta = start` (complete usage from reset) |

### Zero vs Unsupported

The harness distinguishes between:
- **Zero (0)**: Explicitly captured token count, indicates no tokens used
- **Unsupported (null)**: Data not available or not applicable

This distinction is preserved in:
- `start_tokens`: `null` if not recorded at start boundary
- `finish_tokens`: `null` if not recorded at finish boundary
- `delta`: `null` when both ends are unavailable or reset occurred

## Provenance

Each token usage record includes `explicit_provenance` indicating the data source:

| Provenance | Description |
|------------|-------------|
| `thread-id-file` | Parsed from CODEX_THREAD_ID rollout file |
| `explicit-start-boundary` | User-recorded start boundary |
| `explicit-finish-boundary` | User-recorded finish boundary |
| `single-value` | Single snapshot value (e.g., final total) |
| `start-finish-delta` | Calculated from start/finish pair |
| `file-parsed` | Parsed from rolling log file |

## Tuple-Based Aggregation

Sessions sharing the same GitHub issue number are aggregated into summaries:

```json
{
  "session_id": "thread_abc123",
  "issue": 62,
  "repository": "OWNER/REPO",
  "pull_request": 123,
  "environment": "production",
  "total_tokens": 2500,
  "local_tokens": 2400,
  "frontier_tokens": 100,
  "cache_read": 50,
  "cache_write": 25,
  "operation_count": 3,
  "last_activity": "2026-08-16T12:00:00Z",
  "has_concurrent": true,
  "is_interrupted": false,
  "is_missing": false,
  "is_reset": false
}
```

### Aggregation Rules

1. **Token totals**: Sum of finish_tokens across all operations
2. **Cache totals**: Sum of cache_read and cache_write
3. **Concurrent detection**: Multiple sessions with same issue number
4. **State flags**: Boolean indicators for interrupt/missing/reset states

## Dashboard Telemetry

Session data appears in the dashboard when available:

- **Session tokens**: Total tokens across all sessions
- **Interrupted/Missing/Reset counters**: State distribution
- No telemetry shown when sessions data is unavailable

This preserves the dashboard's GET-only behavior and CSP requirements.

## Restart Reconstruction

For recovery after interruption:

1. Existing session records are retained (append-only)
2. New snapshots use the same `session_id` 
3. The harness calculates delta from previous finish_tokens
4. Reset cases are explicitly flagged

This allows accurate reconstruction of token usage even after worker restart.
