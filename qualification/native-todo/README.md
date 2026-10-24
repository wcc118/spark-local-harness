# Native task-list qualification

The worker image pins Qwen Code **0.22.0**. This probe imports its actual
`TodoWriteTool` and Storage implementation; the Storage chunk hash is deliberately
version-specific and unsupported versions or missing chunks fail with exit 1.
It does not reimplement the tool.

Run inside the worker image, where the package is installed:

```bash
python3 qualification/native-todo/probe.py
```

The probe creates a fresh temporary home, calls the native create/update and
validation paths, checks the exact persisted IDs, contents and statuses, rejects
malformed input, verifies unchanged valid state afterward, and lists the session.
It supplies no hook system to this tool instance. This does not disable hooks in
other Qwen sessions or qualify hook behavior.

Results go to stdout by default. `--output /path/results.json` writes a report
explicitly. `--temp-home /existing/parent` creates a fresh child beneath that
parent; only the child is cleaned up. The default leaves no checkout artifacts.
Exit 0 means all checks passed; exit 1 reports failed checks or setup errors.

Todo state is file-backed under the configured runtime directory. The probe
removes its temporary state afterward; production retention depends on the
harness runtime-home lifecycle. Task lists support planning within a worker
session. GitHub issues, commits, PRs and reviews remain authoritative durable
work state; enabling this tool adds no delegation or publication privileges.

The probe does not contact the network or need Docker, but it does **not** impose
network or filesystem confinement itself. Those protections come from the outer
worker container. Host Docker checks must be run by the orchestrator.

Related: [issue #223](https://github.com/wcc118/codex-local-harness/issues/223).
