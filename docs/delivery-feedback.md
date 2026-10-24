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
CODEX_LOCAL_WORKER_V4 ...
```

Default: `CODEX_LOCAL_DELIVERY_FEEDBACK=1` (enabled)

Disable by setting `CODEX_LOCAL_DELIVERY_FEEDBACK=0`.

## How It Works

1. **Installation** (`delivery_feedback.py install`): Copies the checker script to the worker runtime home and appends to `.qwen/settings.json`:
   ```json
   {
     "hooks": {
       "Stop": [
         {
           "hooks": [
             {
               "type": "command",
               "command": "python3 /path/to/runtime-home/delivery-feedback.py check --checkout /path/to/repo --base <base-sha>"
             }
           ]
         }
       ]
     }
   }
   ```

2. **Execution** (`delivery_feedback.py check`): Reads JSON payload from stdin containing:
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

The hook is integrated into `codex-local-worker-v4`:

```bash
# Local feedback is not publication authority. The final host check is unchanged.
case "${CODEX_LOCAL_DELIVERY_FEEDBACK:-1}" in
  0) ;;
  1)
    if [[ $worker_role == coder && $worker_runtime == qwen-code && ${CODEX_LOCAL_CODER_POSTFLIGHT:-1} == 1 ]]; then
      if feedback_base=$(git -C "$worker_repo_path" merge-base origin/main HEAD) && \
        env PYTHONPATH="$candidate_source" python3 -m codex_local_harness.delivery_feedback install \
          --runtime-home "$worker_runtime_home" --checkout "$worker_repo_path" --base "$feedback_base"; then
        worker_task=$'LOCAL DELIVERY FEEDBACK: A native Stop hook reports uncommitted changes and committed-diff whitespace errors before you finish. Use its evidence to repair your own work. If a genuine blocker remains, begin your final response with ESCALATE: and explain it. This feedback never authorizes publication; final host postflight remains authoritative.\n\n'"$worker_task"
      else
        echo "WARN: local delivery feedback setup failed; final host postflight remains authoritative" >&2
      fi
    fi
    ;;
  *) echo "ERROR: CODEX_LOCAL_DELIVERY_FEEDBACK must be 0 or 1" >&2; exit 64 ;;
esac
```

## Testing

Run tests with:
```bash
PYTHONPATH=src python3 -m pytest tests/test_delivery_feedback.py -v
```

Tests cover:
- Clean/dirty/uncommitted checkout detection
- Trailing whitespace, space before tab, new blank line, conflict marker detection
- Generated artifact exclusions (`__pycache__`, `.pytest_cache`, `.pyc`, `.coverage`)
- Rename/copy path handling
- Unusual file paths (spaces, special characters)
- Malformed inputs (invalid JSON, non-dict)
- Invalid/missing base SHA
- Explicit `ESCALATE:` prefix handling
- No-op unrelated events
- Read-only checking preserves Git/worktree state
- Source line contents do not leak (only locations)
- Hook installation preserves existing settings and shell-quotes paths
- Standalone checker actually runs

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
- `bin/codex-local-worker-v4` - Worker entry point with hook installation
- `docs/worker-completion.md` - Stop hook and completion workflow
- `tests/test_delivery_feedback.py` - Comprehensive test coverage
