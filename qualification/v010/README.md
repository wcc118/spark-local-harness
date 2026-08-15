# v0.1.0 diagnostic replay

These sanitized, structured records reproduce four observed states from issue
#19 without retaining prompts, responses, source code, or secrets:

1. routine task independently passed;
2. moderate identity/unhashable task independently passed;
3. concurrency implementation claimed success but failed the hidden evaluator;
4. correction was interrupted by Codex and had no normal worker health record.

Launch the local read-only view from the repository root:

```bash
PYTHONPATH=src python3 -m codex_local_harness.dashboard \
  --runs qualification/v010/replay-runs.jsonl \
  --adjudications qualification/v010/replay-adjudications.jsonl \
  --lifecycle qualification/v010/replay-lifecycle.jsonl \
  --workflow qualification/v011/replay-workflow.jsonl \
  --frontier qualification/v011/replay-frontier.jsonl \
  --mode replay
```

Then open `http://127.0.0.1:8765`. The server rejects non-GET methods and
refuses non-loopback bind addresses.
