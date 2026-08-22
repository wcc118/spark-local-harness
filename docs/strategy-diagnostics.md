# Strategic diagnostics

The dashboard Strategy Lab is a read-only aid for improving the agent
organization. It does not promote routes, mutate prompts or policy, rank
models, or claim that an observed association caused an outcome.

## Evidence layers

- **Facts** are bounded source records: run health, adjudication, lifecycle,
  publication failures, live execution, and optional annotation JSONL.
- **Deterministic derivations** are bottleneck counts, aligned comparison
  stages, and cohort measures calculated from those facts.
- **Annotations** are attributed operator or Codex hypotheses. They remain
  separate from computed facts and never change a metric.

The view considers at most 256 runs, 32 cohorts, and 64 annotations and reports
excluded run history. A cohort with fewer than three runs is labeled
`insufficient sample`.

## Measures and denominators

No composite score is calculated. Every displayed measure includes its
numerator, denominator, exclusions, and source:

- delivery: `delivery_ready=true` divided by runs that report delivery state;
- adjudication: independently passed runs divided by adjudicated runs;
- corrections: observed correction cycles across included runs;
- latency: mean wall seconds among runs with positive observed duration;
- tokens: mean recorded local plus frontier tokens among run-health records;
- tool errors: reported tool errors among runs with observable tool calls;
- infrastructure failure: terminal interruption/cancellation/timeout or an
  explicit infrastructure failure class divided by included runs.

The cohort key is model, profile/route, and task class. Missing values form an
explicit `unknown` cohort rather than disappearing.

## Comparison and bottlenecks

Two selected executions align identity, delivery claim, lifecycle,
adjudication, and correction stages. The first stage with evidence on both
runs and differing values is the first observable divergence. Missing runs or
stages are `incomparable`; later differences are not presented as causes.

Bottlenecks count current inference/tool/test waiting, normalized two-minute
event gaps, correction cycles, publication failures, and unavailable sources.
A count is a diagnostic observation, not a failure verdict.

Optional annotations are newline-delimited JSON records with `annotation_id`,
`kind`, `summary`, `run_ids`, `evidence_refs`, `observed_at`, and `source`.
Configure them with `--annotations`. The dashboard never writes this file.
