# Paired-model benchmark

The paired-model benchmark provides deterministic comparison of model/route combinations
on reproducible local software-engineering tasks. It enables evidence-backed routing
recommendations based on real execution data.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                         Benchmark Components                                 │
├──────────────────────────────────────────────────────────────────────────────┤
│  • Manifest (BenchmarkManifest): Versioned configuration for benchmark runs  │
│  • Fixture (BenchmarkFixture): Reproducible task with identity hash          │
│  • Attempt (BenchmarkAttempt): Single run with metrics and outcomes          │
│  • PairedSummary: Model comparison with correctness-first rules              │
│  • BenchmarkMetrics: Aggregate statistics per model/route/category           │
└──────────────────────────────────────────────────────────────────────────────┘
```

## CLI Usage

### Create benchmark manifest

```bash
PYTHONPATH=src python3 -m codex_local_harness.benchmark_cli manifest \
  --output ~/.local/share/codex-local-harness/benchmark/manifest.json \
  --fixtures-dir ~/.local/share/codex-local-harness/benchmark/fixtures \
  --description "My paired-model benchmark"
```

### Create fixtures

Fixtures cover 8 categories: investigation, focused_fix, tests, ui, refactoring,
diagnosis, hygiene, and review. Each fixture has a deterministic identity hash.

```bash
PYTHONPATH=src python3 -m codex_local_harness.benchmark_cli fixture \
  --output ~/.local/share/codex-local-harness/benchmark/fixtures/issue-42.json \
  --id issue-42 \
  --category focused_fix \
  --prompt "Fix the bug where hash collisions are incorrectly treated as duplicates" \
  --context '{"module": "hash_table.py", "line": 142}'
```

### Run benchmark attempts

```bash
PYTHONPATH=src python3 -m codex_local_harness.benchmark_cli run \
  --manifest ~/.local/share/codex-local-harness/benchmark/manifest.json \
  --model qwen-coder-v3 \
  --route default \
  --worker-command "PYTHONPATH=src python3 -m codex_local_harness.v5_runner --model qwen-coder-v3 --route default"
```

### Compare paired runs

```bash
PYTHONPATH=src python3 -m codex_local_harness.benchmark_cli compare \
  --left /path/to/left-attempts.jsonl \
  --right /path/to/right-attempts.jsonl \
  --output ~/.local/share/codex-local-harness/benchmark/summaries.jsonl
```

### Compute aggregate metrics

```bash
PYTHONPATH=src python3 -m codex_local_harness.benchmark_cli metrics \
  --attempts ~/.local/share/codex-local-harness/benchmark/attempts.jsonl \
  --output ~/.local/share/codex-local-harness/benchmark/metrics.jsonl \
  --min-sample 2
```

### Generate dashboard snapshot

```bash
PYTHONPATH=src python3 -m codex_local_harness.benchmark_cli snapshot \
  --manifest ~/.local/share/codex-local-harness/benchmark/manifest.json \
  --attempts ~/.local/share/codex-local-harness/benchmark/attempts.jsonl \
  --summaries ~/.local/share/codex-local-harness/benchmark/summaries.jsonl \
  --output ~/.local/share/codex-local-harness/benchmark/snapshot.json
```

## Metrics

The benchmark records:

- **Model/Route**: Identifies the model and routing configuration
- **Case/Fixture/Prompt Identity**: Deterministic hash for matching across models
- **Timing/State**: Wall time and completion state (completed/failed/interrupted)
- **Deterministic Correctness**: Pass/fail verdict based on adjudication
- **Tools/Failures**: Tool call count and error rate
- **Permissions/Questions/Interventions**: Control plane interaction metrics
- **Correction Cycles**: Number of refinement iterations
- **Token Usage**: Input/output/reasoning tokens (when available)
- **Run IDs**: Reference to raw execution records
- **Reviewer Findings**: Evidence references to review artifacts

Missing data remains explicitly unavailable rather than zero.

## Routing Recommendations

Recommendations derive from paired summaries with explicit rules:

1. **Minimum Sample**: At least 2 comparable attempts required before recommendation
2. **Correctness-First**: If one model succeeds and another fails, the successful
   model is recommended regardless of token efficiency
3. **Tie/Inconclusive**: If both succeed or both fail, compare tokens/time; prefer
   lower consumption when outcomes are equivalent

The dashboard displays these recommendations as outcome cards with raw-run links
for deeper inspection.

## Dashboard View

The benchmark section appears below the strategy lab in the dashboard:

```
Paired-model benchmark
[Fixture selector] [Model selector] [Category selector] [Refresh]

┌─────────────────────────────────────────────────────────────────┐
│ issue-42                                                        │
│ ✓ Left model correct-first                                      │
│                                                                 │
│ Model A                      Model B                            │
│ qwen-coder-v3                claude-3.5                         │
│ Tokens A: 1,234,567          Tokens B: 987,654                 │
│ Elapsed A: 145.3s            Elapsed B: 178.2s                 │
│ ✓ A                          ✗ B                              │
│                                                                 │
│ Analysis: correctness-first: left succeeded, right failed       │
│ [Raw run A] [Raw run B]                                         │
└─────────────────────────────────────────────────────────────────┘
```

## Safety and Privacy

- Raw prompts and outputs are never stored in summaries
- Evidence references are bounded identifiers, not captured content
- Token usage is reported only when available (unsupported when not measured)
- Permissions and questions counts are recorded but never raw values
- All sensitive data stays in operator-owned directories

## Integration with Dashboard

The benchmark section is loaded from:

- `benchmark_manifest`: JSON manifest file
- `benchmark_attempts`: JSONL attempts with BenchmarkAttempt records
- `benchmark_summaries`: JSONL paired summaries with PairedSummary records

Missing files are treated as empty rather than failing validation.

## Testing

Run benchmark tests:

```bash
PYTHONPATH=src python3 -m pytest tests/test_benchmark.py -v
```

Run full test suite:

```bash
PYTHONPATH=src python3 -m pytest tests -v
```
