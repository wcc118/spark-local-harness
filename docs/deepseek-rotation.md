# Local model routing

Coder-Next remains the default coder. DeepSeek reasoning is the default reviewer
and the model used by `codex-local-investigator`. DeepSeek coding is opt-in:

```bash
codex-local-coder /path/to/repository --task-file issue.md
codex-local-investigator /path/to/repository --task-file investigation.md
CODEX_LOCAL_TIER=deepseek codex-local-coder /path/to/repository --task-file issue.md
```

For a review, supply the normal durable issue/PR identifiers:

```bash
CODEX_LOCAL_REPOSITORY=OWNER/REPO CODEX_LOCAL_ISSUE=123 CODEX_LOCAL_PR=124 \
  codex-local-reviewer /path/to/repository --task-file review.md
```

Investigation uses the existing helper permissions and read-only repository mount.
The ordinary helper route is preserved. Coder and reviewer run sequentially on
an issue, and Codex retains orchestration and final judgment.

The worker prepends an execution contract to DeepSeek tasks. Coding uses the
four-phase inspect/edit/verify/report approach. Review and investigation use
the role and uncertainty guidance from the qualification experiment, adapted
to production output schemas. Benchmark-only labels and `result.json` rules
are not injected into real tasks. Issue facts and acceptance criteria remain
authoritative. Both native execution and the session bridge receive the final
packet, including any prepared reviewer evidence.

The existing model lock is held throughout each worker run. The manager leaves
the selected model loaded until a later worker requests another profile:

```bash
codex-local-model use deepseek-v4-reasoning
codex-local-model use coder-next
codex-local-model status
```

Coder-Next runs in `vllm_node` on port 8000 with context 185000 and GPU utilization
0.82 by default. DeepSeek runs in its pinned EXL3 Compose image on port 8011, with exact
served identity `deepseek-v4-flash-0731-exl3-k2`, context 1000000, GPU utilization
0.85, and thinking enabled at high reasoning effort. It cannot be launched by
adding EXL3 flags to the Coder-Next image. The manager stops the current container
before starting the other runtime, waits for exact model readiness, and returns
an infrastructure error on failure. It does not restore or retry automatically.
When `vllm_node` was automatically removed on stop, the manager recreates it
using the existing `qwen3-coder-next-fp8` recipe before checking readiness.

DeepSeek startup requires the cached pinned image and recipe. The reasoning
overlay is `config/deepseek-v4-reasoning.yaml`; the launcher also accepts
`CODEX_LOCAL_DEEPSEEK_OVERLAY` for an explicit installed location.

The vLLM runtime supports GPU memory control via the optional `CODEX_LOCAL_VLLM_GPU_MEM`
environment variable. When unset, the manager defaults to `0.82` for Coder-Next and
qwen38 profiles. The `qwen35-nvfp4` profile keeps its own fixed `0.82` launch value. All `use` commands validate the
setting before touching containers, including switches to other profiles.
Valid values are finite numbers in the interval `(0.5, 0.95]`. An empty value,
nonfinite value (NaN, Inf), or value outside the range causes the launch to fail
before any container mutation. This setting does not affect the DeepSeek backend,
which uses its own pinned recipe with `0.85`.

Installation must update the worker, manager, DeepSeek launcher, investigator
entry point, routing JSON, and reasoning overlay together. Preserve a backup of
the installed files. Deploying the routes alone is insufficient.

## Issue 167 validation

Codex completed implementation and final review directly. Focused switching,
routing, confinement, and prompt-delivery tests: 26 passed. Full harness suite:
547 passed, one existing v0.6 historical freeze-checksum failure. The separate
benchmark fixture suite passed all five tests.

Live checks verified Coder-Next to DeepSeek startup, exact DeepSeek identity,
thinking/high configuration, a successful completion, and idempotent reuse.
The return switch verified recreation of the auto-removed Coder-Next container
through the production recipe, exact identity, context 185000, GPU utilization
0.87, and a successful completion. The initial return-switch failure exposed
the auto-removal behavior; it was corrected and covered by a regression test.

The installed worker hash is recorded in `config/installed-release.json`.
The previous installed worker, v4 worker, manager, and routing configuration are
backed up at
`~/.local/share/codex-local-harness/issue-167-backup.XyGhox/`.
