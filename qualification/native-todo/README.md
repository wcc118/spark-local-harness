# Native Todo_write Qualification

## Overview

This directory contains a qualification probe for the native `todo_write` tool that's built into Qwen Code (v0.22.0+).

## Purpose

The probe exercises the **actual installed** `TodoWriteTool` implementation from:
```
/usr/local/lib/node_modules/@qwen-code/qwen-code/chunks/todoWrite-*.js
```

It does **not** implement or mock the tool. It imports and runs the real code with:

- A separate temporary home directory (`QWEN_RUNTIME_DIR`)
- No hooks configured (`getHookSystem() → null`)
- No Docker socket, network, or installation needed
- Pure Node.js execution with native package

## Session-Local Planning vs. Durable GitHub Work State

### Session-Local Planning (native todo_write)

The `todo_write` tool stores task lists **in-memory at runtime** under the session's runtime directory:

```
$QWEN_RUNTIME_DIR/todos/{sessionId}.json
```

Key characteristics:

1. **Temporary scope**: Data exists only during the session lifetime
2. **No persistence across sessions**: Each new session gets a fresh workspace
3. **No remote sync**: Nothing is pushed to GitHub or external storage
4. **No hooks**: The probe explicitly disables hooks for clean validation
5. **Validation-only**: The tool validates malformed input and rejects it

This is appropriate for:
- Long-running interactive sessions where the user wants progress tracking
- Multi-step tasks within a single conversation
- Session-local task lists that don't need GitHub issue synchronization

### Durable GitHub Work State

GitHub-based work state (issues, PRs, tasks) is managed through:

1. **Remote repository**: Actual work tracked in git history
2. **Issue/PR numbers**: External reference for collaboration
3. **Commit history**: Immutable record of changes
4. **Pull request workflow**: Formal review and merge process

This is appropriate for:
- Collaborative development across team members
- Permanent records of design decisions
- Audit trails for production changes
- Integration with CI/CD and release workflows

### When to Use Each

| Use Case | Recommendation |
|----------|---------------|
| Session-local progress tracking | `todo_write` |
| Multi-step task within one conversation | `todo_write` |
| Team collaboration | GitHub issue/PR |
| Permanent record | GitHub issue/PR |
| Design decision tracking | GitHub ADR |
| Production deployment | GitHub release |

## Probe Execution

### Basic Run

```bash
python qualification/native-todo/probe.py
```

### With Custom Output

```bash
python qualification/native-todo/probe.py --output results.json
```

### Keep Temp Directory (for debugging)

```bash
python qualification/native-todo/probe.py --temp-home /tmp/my-test-home
```

## Validation Coverage

The probe validates:

1. **Create**: Valid todos are written to session file
2. **Update**: Existing todo list can be modified
3. **Readback**: Persisted state is correctly retrieved
4. **Validation rejection**: Malformed todos are rejected
5. **State preservation**: Failed validation doesn't corrupt prior state

## Expected Exit Behavior

- **Exit 0**: All tests passed, temp home cleaned up
- **Exit 1**: One or more tests failed, temp home cleaned up
- **Exit N**: Unexpected error (e.g., missing todoWrite chunk)

## Limitations

1. **Docker-dependent checks**: Tests that require Docker are host-only and report as skipped
2. **No network**: Probe runs in isolated environment without network access
3. **No hooks**: Hook validation requires the orchestrator configuration

## Related References

- Issue: wcc118/codex-local-harness#223
- Commit: ab7f23a (add todo_write to Qwen tools.core)
- Chunk: `/usr/local/lib/node_modules/@qwen-code/qwen-code/chunks/todoWrite-*.js`
- Storage: `chunk-A5F2YNO6.js` (runtime base dir management)
