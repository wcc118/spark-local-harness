# Local Delivery Feedback

Advisory native Stop hook for deterministic delivery feedback. Reports uncommitted changes and committed-diff whitespace errors before the native Qwen coder exits, allowing agent-owned repair rather than frontier relay.

## Overview

The delivery feedback module provides a stdlib-only hook that reads Git state during a native Qwen coder session. It reports issues via a `Stop` hook, giving the agent a chance to fix problems before finishing.

**Key principles:**
- Advisory only; final host postflight (`finalize_coder` in `coder.py`) remains authoritative
- Never repairs, commits, publishes, or authorizes delivery
- Read-only checking preserves Git/worktree state
- No source line contents leak in feedback - only file locations
- Runtime's existing Stop blocking-cap behavior remains unchanged (not a new whole-task timer)

## Configuration

The hook is installed automatically for native Qwen coder sessions when:

```bash
CODEX_LOCAL_DELIVERY_FEEDBACK=1 \
CODEX_LOCAL_CODER_POSTFLIGHT=1 \
codex-local-coder REPOSITORY_PATH --task-file TASK_FILE
```

Default: `CODEX_LOCAL_DELIVERY_FEEDBACK=1` (enabled)

Disable by setting `CODEX_LOCAL_DELIVERY_FEEDBACK=0`.

## How It Works

1. **Installation** (via `install()`): Copies `delivery-feedback.py` to the worker runtime home and appends to `.qwen/settings.json` for the native worker's Stop event.

2. **Execution** (via `check()`): Reads JSON payload from stdin containing:
   ```json
   {
     "hook_event_name": "Stop",
     "last_assistant_message": "..."
   }
   ```

3. **Output**: Returns JSON decision:
   - **Clean**: `{}` (no issues)
   - **Block with reason**: `{"decision": "block", "reason": "..."}`
   - **Escalate**: `{"systemMessage": "Coder reported escalation; final host postflight still runs."}`

## Detectable Issues

### Uncommitted Checkout Changes
Files modified, added, or deleted but not committed:
```
Uncommitted checkout changes (1): "dirty.txt"
```

### Committed Diff Check Failures
Whitespace issues detected via `git diff --check <base>..HEAD`:
- **trailing whitespace**: Lines ending with spaces
- **space before tab**: Space character before a tab character
- **new blank line**: New empty line at end of file
- **leftover conflict marker**: Unresolved merge conflict markers

Example:
```
Committed diff check failed: src/value.py:15: trailing whitespace.
```

### Generated Artifact Exclusions
The following paths are excluded from uncommitted changes:
- `__pycache__/` directories and `*.pyc` files
- `.pytest_cache/` directories
- `.coverage` and `.coverage.*` files

## Output Format

### Clean Checkout
```json
{}
```

### Dirty Checkout
```json
{
  "decision": "block",
  "reason": "LOCAL DELIVERY FEEDBACK (advisory; host postflight will independently recheck):\nUncommitted checkout changes (1): \"dirty.txt\"\n\nInspect and correct your intended work, run relevant verification, and commit it before finishing. Preserve unrelated work. Do not push or publish. If a genuine blocker prevents completion, start the final response with ESCALATE: and explain the concrete blocker; this does not bypass host verification."
}
```

### Whitespace Error
```json
{
  "decision": "block",
  "reason": "LOCAL DELIVERY FEEDBACK (advisory; host postflight will independently recheck):\nCommitted diff check failed: src/value.py:15: trailing whitespace.\n\nInspect and correct your intended work, run relevant verification, and commit it before finishing. Preserve unrelated work. Do not push or publish. If a genuine blocker prevents completion, start the final response with ESCALATE: and explain the concrete blocker; this does not bypass host verification."
}
```

### Escalate Prefix
When `last_assistant_message` starts with `ESCALATE:`:
```json
{
  "systemMessage": "Coder reported escalation; final host postflight still runs."
}
```

### Unavailable
When Git state cannot be inspected:
```json
{
  "systemMessage": "Local delivery feedback unavailable; final host postflight remains authoritative."
}
```

## CLI Usage

### Install Hook
```bash
python3 -m codex_local_harness.delivery_feedback install \
  --checkout /path/to/repo \
  --base <base-sha> \
  --runtime-home /path/to/runtime-home
```

### Check State
```bash
echo '{"hook_event_name": "Stop", "last_assistant_message": ""}' | \
  python3 -m codex_local_harness.delivery_feedback check \
    --checkout /path/to/repo \
    --base <base-sha>
```

## Integration Points

The hook is integrated into `codex-local-coder` worker runtime via the worker framework's Stop hook installation mechanism. The worker's built-in `finalize_coder` postflight remains the final authority for delivery decisions.

**Escalate handling:** If the coder reports `ESCALATE:`, delivery feedback returns `systemMessage` to allow escalation handling, but the final host `finalize_coder` still performs its own Git checks and may reject dirty/whitespace state.

**Runtime cap vs task deadline:** This delivery feedback mechanism is independent of task deadlines. It provides advisory local feedback before the coder exits, not a new whole-task timer.

## Testing

Run tests with:
```bash
PYTHONPATH=src python3 -m pytest tests/test_delivery_feedback.py -v
```

Tests cover:
- Clean/dirty/uncommitted checkout detection
- Trailing whitespace, space before tab, new blank line, conflict marker detection
- Generated artifact exclusions (`__pycache__`, `.pytest_cache`, `.pyc`, `.coverage`)
- Staged rename and dirty unusual filename with actual statuses
- Unusual file paths (spaces, special characters)
- Malformed inputs (invalid JSON, non-dict)
- Invalid/missing base SHA
- Explicit `ESCALATE:` prefix handling
- No-op unrelated events
- Read-only checking preserves Git/worktree state
- Source line contents do not leak (only locations)
- Hook installation preserves existing settings and shell-quotes paths
- Standalone checker actually runs with actual worker setup wiring

## Rollback

To disable delivery feedback:

1. Set `CODEX_LOCAL_DELIVERY_FEEDBACK=0` in the worker configuration
2. Or remove the `Stop` hook from `.qwen/settings.json` in the runtime home

The worker's built-in postflight (`finalize_coder`) remains active and will still enforce the same checks.

## Security Boundaries

- No network calls
- No source line content in feedback (only file/line locations)
- No mutation of worktree or Git state
- No publication authority
- No credentials or secrets
- Shell-quoted paths prevent injection via unusual filenames

## See Also

- `src/codex_local_harness/coder.py` - Final host postflight with same Git checks
- `bin/codex-local-coder` - Worker entry point with runtime initialization
- `docs/worker-completion.md` - Stop hook and completion workflow
- `tests/test_delivery_feedback.py` - Comprehensive test coverage
