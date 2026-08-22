# V5 thin local runner

The v5 runner treats the operator's existing workspace or machine sandbox as
the security boundary. It runs OpenCode directly with the caller's normal
environment and developer tools. It does not create a container, replace
`HOME`, remove credentials, or generate command permission rules.

The runner is intentionally small. It prepares a deterministic issue branch,
streams OpenCode text and tool events to the terminal, retains the complete
JSONL event stream, and writes an observed result summary. It does not infer
test success from model prose.

## Run a coder

```bash
bin/codex-local-worker-v5 /path/to/repository \
  --issue 98 \
  --title "dashboard operational verdict" \
  --task-file /path/to/issue-packet.md
```

The default model is the complex local coder and the default agents are
`v5-coder` and `v5-reviewer`. Both agents have unrestricted OpenCode tool
permissions. Override them with `--model`, `--agent`, or `CODEX_LOCAL_MODEL`.
The caller's `PATH`, Python
environment, OpenCode configuration, credentials, and other environment values
are preserved.

Logs and summaries default to:

```text
~/.local/share/codex-local-harness/v5/runs/
```

Codex or an operator can monitor a running session without changing it:

```bash
tail -f ~/.local/share/codex-local-harness/v5/runs/RUN_ID.jsonl
```

An interrupted or unsuccessful run is not cleaned up. Inspect the repository's
branch, working tree, retained JSONL, and result JSON, then rerun the same issue
command to resume with the existing dirty worktree.

## Review

Run the same entry point with `--role reviewer`, `--expected-head SHA`, and an
exact review packet. Reviewer mode validates the current commit and never
creates or switches branches. The
first v5 slice does not publish GitHub state automatically. Pushes, pull
requests, comments, labels, and merges remain ordinary explicit operator
actions after inspecting the result.

## Migration from v4

The v5 entry point is installed alongside v4 during qualification. It does not
use v4's Docker worker, isolated clone, disposable OpenCode home, command
allowlist, role pipeline, or automatic cleanup. Historical ledgers and the v4
dashboard remain readable and unchanged.
