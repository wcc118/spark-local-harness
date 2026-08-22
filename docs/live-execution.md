# Live execution projection

The dashboard can project retained v5 worker events into a bounded, read-only
topology. Pass the v5 run directory with `--v5-runs`. The default deployment
uses `~/.local/share/opencode-orchestration/runs`.

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

## Agent brain view

The visual topology is an execution model, not a picture of literal model
cognition. It never displays private reasoning or chain-of-thought. Its stable
regions map observed evidence as follows:

- **Intake + repository** — retained checkout identity and task boundary.
- **Codex executive** — official orchestration evidence when available.
- **Routing + inference** — retained model and endpoint identity.
- **Coder + action** — local worker execution, including helper/coder roles.
- **Review + verification** — reviewer roles and recognized test activity.
- **Tools** — normalized external tool use without commands or output.
- **Durable memory** — issue/workflow/result evidence when supplied.

An empty region means evidence is absent; it does not mean that component is
idle. Signal animation marks a recent, observable directed relationship only.
It does not imply hidden communication. Selecting a node or signal exposes its
state, observation age, derivation reason, and retained source reference. The
same interaction works by keyboard, and reduced-motion preferences disable
signal animation without removing state text.

## Execution microscope

Selecting a brain node, signal, chronological event, attention item, diagnostic
run, or workflow operation updates the same read-only evidence inspector. The
surface shows only normalized fields that exist: run/session/issue/repository,
role and model, phase and elapsed time, tool/test counts, observed files,
branch/commit/PR references, token evidence, liveness reason, observation age,
and source reference. Unsupported fields say `Unavailable`; they are never
estimated.

The chronological rail contains at most 160 normalized events. Terminal result
availability is an event, but does not itself verify semantic acceptance.
Acceptance therefore remains `unknown` unless an independent criterion source
reports `evidence-observed`, `contradicted`, or `verified`. Handoffs mean only
that a retained worker result became available to Codex; the UI does not infer
an unobserved coder-to-reviewer conversation.

Live diagnostics now fingerprint observable tool name/input pairs and emit an
`exploration-loop` warning after three distinct calls repeat the same request.
Lifecycle updates for one call ID are deduplicated. Fingerprints are hashes;
command bodies and paths are not copied into the projection.

The bridge also classifies observable activity into `investigation`,
`implementation`, `testing`, or `review` and emits `stage-budget` warnings at
80 percent and critical signals after the configured tool, token, or wall-time
budget. Defaults are defined in `execution_diagnostics.py`; operators can set
`CODEX_LOCAL_STAGE_BUDGETS` to a JSON object containing per-stage overrides.
These are diagnostic budgets, not inferred failure verdicts or private thought
timers. The dashboard uses them to show where work is consuming its allowance.

Other neutral notices call out tool-reported errors and intervals of at least
two minutes between retained events. Command bodies, test
commands, prompts, output, and private reasoning are excluded. File names are
bounded to retained changed-path evidence. Raw artifact download is disabled;
source references are identifiers rather than browser-accessible file paths.
