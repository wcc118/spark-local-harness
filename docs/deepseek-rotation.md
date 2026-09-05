# DeepSeek V4 Flash Rotation

## Overview

DeepSeek V4 Flash serves as the default reviewer and read-only investigator in the Codex Local Harness. It is configured as the default model for reviewer routes and is never automatically selected for coding tasks.

## Operating Behavior

### Model Configuration

| Profile | Model Name | Role | Auto-Select | Configuration |
|---------|-----------|------|-------------|---------------|
| `coder-next` | `Qwen/Qwen3-Coder-Next-FP8` | Coder-Next (standard) | **Yes** (default) | `0.87` GPU mem, `185000` max len |
| `deepseek-v4-reasoning` | `deepseek-v4-reasoning` | Reviewer/Investigator | **Never** | `0.85` GPU mem, `1000000` max len |

### Default Routing

The `config/workers-vllm.json` routes are configured as follows:

- **Coder routes** (helper, coder, reviewer) → `vllm/Qwen/Qwen3-Coder-Next-FP8`
- **Reviewer routes** → `vllm/deepseek-v4-reasoning` (default)
- **DeepSeek routes** → Explicit opt-in only

### DeepSeek Contract

The reviewer uses the `role-contract-plus-uncertainty` contract which:
- Accepts immutable issue, PR, commit, and evidence in the task
- Returns structured JSON verdict (`APPROVE`, `REQUEST_CHANGES`, `ESCALATE`)
- Never edits, publishes, approves, merges, or changes GitHub state
- Identifies evidence as `harness-observed`, `reviewer-observed`, or `task-reported`

## Manual Invocation

### Switching to DeepSeek

To explicitly switch the model manager to DeepSeek V4 Flash:

```bash
codex-local-model use deepseek-v4-reasoning
```

This will:
1. Acquire the model lock
2. Stop any currently active vLLM server
3. Launch DeepSeek V4 Flash with optimized Spark settings
4. Wait for model readiness
5. Verify exact model identity

### Returning to Coder-Next

To switch back to Coder-Next:

```bash
codex-local-model use coder-next
```

The model manager:
1. Stops DeepSeek first
2. Then launches Coder-Next
3. Is idempotent (no-op if already active)

### DeepSeek Explicit Coder Route

To use DeepSeek for coding (opt-in only, never auto-selected):

```bash
CODEX_LOCAL_TIER=standard CODEX_LOCAL_MODEL=vllm/deepseek-v4-reasoning \
codex-local-worker coder /path/to/repo "task description"
```

Or using the explicit route:

```bash
CODEX_LOCAL_TIER=standard \
codex-local-worker coder /path/to/repo "task description"
```

With `config/workers-vllm.json` route:
```json
{
  "deepseek/standard": {
    "agent": "reviewer",
    "runtime": "opencode",
    "mode": "native",
    "model": "vllm/deepseek-v4-reasoning"
  },
  "deepseek/coder": {
    "agent": "coder",
    "runtime": "opencode",
    "mode": "tailored",
    "model": "vllm/deepseek-v4-reasoning"
  }
}
```

## Safety Features

1. **Exclusive Lock**: The model manager uses a file lock to prevent concurrent model switches
2. **Fail Closed**: If the exact model doesn't become ready, the command exits with code 69
3. **Stop Before Start**: DeepSeek is stopped before Coder-Next (and vice versa)
4. **Idempotent**: Repeated calls with the same profile are no-ops
5. **Read-Only Reviewer**: The reviewer never modifies the checkout

## DeepSeek Runtime Wrapper

The `bin/codex-local-deepseek-v4` wrapper provides additional safety:

```bash
# Pull the pinned image
codex-local-deepseek-v4 pull

# Check preflight conditions
codex-local-deepseek-v4 preflight

# Start the candidate
codex-local-deepseek-v4 start

# Check status
codex-local-deepseek-v4 status

# Stop the candidate
codex-local-deepseek-v4 stop
```

**Important**: This wrapper is for external use only. The model manager handles DeepSeek internally.

## Verification

### Check Active Model

```bash
codex-local-model status
```

Expected response:
```json
{"status":"ready","model":"deepseek-v4-reasoning"}
```

### Verify Routing Configuration

```bash
jq '.routes["reviewer/standard"]' config/workers-vllm.json
```

Expected:
```json
{
  "agent": "reviewer",
  "runtime": "opencode",
  "mode": "native",
  "model": "vllm/deepseek-v4-reasoning"
}
```

## Known Limitations

1. **No Automatic Switching**: DeepSeek is never automatically selected for coding tasks
2. **Manual Opt-in Required**: DeepSeek coding requires explicit route configuration
3. **Read-Only Reviewer**: The reviewer cannot modify files or execute commands
4. **DeepSeek Model Name**: Uses `deepseek-v4-reasoning` as a placeholder name; the actual vLLM model is identified by its trained weights
