# Paired-model software-engineering benchmark

This corpus compares models on ten identical, isolated tasks. The worker sees
only `input/` and one task packet. `oracle.py` remains outside the workspace.
Every attempt records source, task, check, and permission-profile hashes.

Run one model after loading it into vLLM:

```bash
python3 qualification/paired-model/run.py \
  --output ~/.local/share/codex-local-harness/benchmark run \
  --model vllm/Qwen/Qwen3-Coder-Next-FP8 --route coder/complex
```

Run the second model, then generate comparison and dashboard artifacts:

```bash
python3 qualification/paired-model/run.py \
  --output ~/.local/share/codex-local-harness/benchmark compare \
  --left vllm/Qwen/Qwen3-Coder-Next-FP8 \
  --right vllm/Qwen/Qwen3.8-27B-FP8
```

The 2026-08-22 controlled run produced 10/10 correct for Coder Next and 0/10
for Qwen 3.8; every Qwen attempt timed out. Raw events and workspaces remain in
the operator data directory and are intentionally not committed.

Coder Next remains the default. To run a deliberate Qwen 3.8 experiment, use
`CODEX_LOCAL_TIER=experimental-qwen38` with the appropriate stable worker entry
point. This tier is retained for diagnosis and is not production-qualified.

## Prompt/policy qualification

Policy variants use the same frozen source, tasks, checks, permission profile,
and model. A variant changes only an explicit instruction prefix and gets a
separate attempt directory:

```bash
python3 qualification/paired-model/run.py \
  --output ~/.local/share/codex-local-harness/benchmark run \
  --model vllm/Qwen/Qwen3-Coder-Next-FP8 --route coder-next-compact-v1 \
  --variant compact-v1 \
  --instruction-prefix qualification/paired-model/policies/compact-v1.md

python3 qualification/paired-model/run.py \
  --output ~/.local/share/codex-local-harness/benchmark select \
  --model vllm/Qwen/Qwen3-Coder-Next-FP8 \
  --baseline baseline --candidate compact-v1
```

Selection is fail-closed: both cohorts must have ten completed oracle-passing
attempts, the candidate must be non-worse in every aggregate cost, and at least
one cost must improve. The first `compact-v1` run was rejected: two cases timed
out, and it increased elapsed time, tools, tool errors, and tokens. The durable
decision is in `results/policy-selection.json`.
