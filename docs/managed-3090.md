# Optional managed 3090 candidate

This route is not qualified for promotion. It does not change production defaults.
Use only for owner-authorized, bounded ordinary coding outside the critical path.
Architecture, security, secrets, migrations and public API decisions remain frontier
work. The shadow critic protocol remains a separate read-only qualification.

The candidate workers configuration includes `coder/managed-3090` using native
Qwen Code and `lmstudio/qwen/qwen3.8-27b`. To exercise admission without inference:

```
CODEX_LOCAL_WORKER_CONFIG=config/workers-vllm.json CODEX_LOCAL_DRY_RUN=1 \
PYTHONPATH=src python3 -m codex_local_harness.managed_lane REPOSITORY TASK
```

The entry point checks `/api/v0/models` for the exact loaded model and at least
65536 loaded context, while holding the existing lane-3090 file lease. Missing,
unreachable, insufficient-context or busy lanes record a skipped admission and
exit0 without creating a worker, changing a model, or blocking the main task.
A downloaded model listed by `/v1/models` alone is not sufficient.

For actual execution remove DRY_RUN, set the normal repository/issue/session
identity, and use a clean issue checkout. The gate records model and loaded context
in `lane-admissions.jsonl`, then uses normal supervised worker confinement,
publication, shared-memory credentials and completion ownership. It forces a
900-second deadline,40 session turns,80 completed-tool-call budget,8192 output-token cap, and no
Spark switch. The lane lease remains held through the execution. Private oracle
memory is never mounted or provisioned for this worker. Memory runs on the Spark;
LM Studio on the PC supplies inference and needs no database credentials.

Review the admission disposition, not just exit0: skipped is not completed work.
Existing worker failures remain failures. Loaded state is a preflight observation,
not a guarantee against the owner unloading a model later. The advisory lease
coordinates harness callers, not manual PC actions. Do not auto-load models.

Rollback is one step: stop using this explicit entry point and tier; standard
coder/reviewer routes are untouched. Keep the legacy OpenCode provider until
managed-route corpus and shadow qualification succeed. Record live qualification
as unavailable when no model is loaded; simulated admission is not a live pass.
