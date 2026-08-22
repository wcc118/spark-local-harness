# Live execution projection

The dashboard can project retained v5 worker events into a bounded, read-only
topology. Pass the v5 run directory with `--v5-runs`. The default deployment
uses `~/.local/share/codex-local-harness/v5/runs`.

The `live_execution` snapshot member contains normalized nodes, edges, and
events. It reports worker, model, repository, tool, and test relationships and
uses the states `waiting-inference`, `waiting-tool`, `running-tests`, `stale`,
`completed`, `failed`, `interrupted`, and `unknown`. Every item carries an
observation time, a retained-evidence reference, and a short reason.

This projection intentionally excludes prompts, command text, tool output, and
secrets. Malformed or partially written records are ignored and diagnosed
without breaking the dashboard. Results are deterministically ordered and
bounded so an old or noisy run directory cannot grow the snapshot without
limit. The v5 files remain the source of truth; the dashboard never writes to
them or controls a worker.

## Evidence and uncertainty

Terminal `.result.json` evidence takes precedence over the last event in the
matching `.jsonl` log. Without a result, the newest normalized event determines
the waiting/tool/test state; after five minutes without a file update, that run
is `stale`. A missing source is `missing` or `unavailable`, malformed records
are diagnosed, and absent identity fields remain `null` or omitted rather than
being guessed. Event timestamps describe the observation, while file freshness
determines whether an otherwise non-terminal run can still be called live.

Repository, role, model, and issue identity are included only when retained
evidence provides them. Current v5 result files provide repository, role, and
model identity after termination; active logs still expose the running worker
and its observable tool/test transitions without inventing unavailable labels.
