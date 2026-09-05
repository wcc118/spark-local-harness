# DeepSeek Prompt-Ablation Experiment v3

This module extends role-suite v2 with a controlled DeepSeek prompt-ablation experiment,
isolating the effect of role-specific contract variations on DeepSeek's structured output.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    Prompt-Ablation Experiment Design                         │
├──────────────────────────────────────────────────────────────────────────────┤
│  • Challenge Set: 28 cases (DeepSeek misses + clean controls + anchors)     │
│  • Policy Variants: 4 variants for DeepSeek                                 │
│  • V2 Corpus: Preserved frozen (62 cases, no modification)                  │
│  • Collision-Safe Artifacts: Identity-validated with oracle separation      │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Challenge Subset

The challenge subset contains **28 deterministic cases** selected to:

1. **DeepSeek Misses (17 cases)**: All cases DeepSeek got wrong in v2 structured run
   - `review-single-06` through `review-interacting-04`
   - `investigate-02`, `investigate-04`, `investigate-07`, `investigate-08`
   - `test-strategy-01`, `test-strategy-03`, `test-strategy-05`
   - `noncoding-long-context-04`, `noncoding-architecture-03`
   - `noncoding-specification-04`, `noncoding-reconciliation-02`
   - `noncoding-operations-02`, `workflow-02`, `workflow-03`

2. **Ambiguous Clean Controls (2 cases)**: Reviewer clean controls with ambiguous scoring
   - `review-clean-01`: NULL_GUARD (None return interpretation)
   - `review-clean-04`: ERROR_SWALLOW (False return interpretation)
   - **Excluded from promotion scoring** (see below)

3. **Passing Anchors (13 cases)**: Representative cases DeepSeek got right
   - Basic single-defect cases
   - Interacting defects
   - Investigation and workflow cases
   - Non-coding analysis

See `challenge_set.py` for complete selection criteria and metadata.

## Policy Variants

Four policy variants for DeepSeek evaluation:

| Policy | Category | Description |
|--------|----------|-------------|
| `structured-v1` | baseline | Existing v2 4-phase structured policy (unchanged) |
| `role-contract` | role-specific | Clear role-specific contract with 5-step process |
| `role-contract-plus-verification` | role-specific+verification | Contract plus explicit omission/false-positive verification |
| `role-contract-plus-uncertainty` | role-specific+uncertainty | Contract plus explicit uncertainty assessment (high/medium/low confidence) |

All policies include role-specific prompts for:
- reviewer: Code review for defects, security, concurrency
- investigator: Production incident root cause analysis
- test-strategy: Test coverage and regression analysis
- non-coding: Documentation and specification analysis
- end-to-end: Workflow orchestration

## CLI Usage

### Verify challenge subset

```bash
PYTHONPATH=src python3 -m qualification.role_suite.v3.runner verify
```

Output:
```json
{
    "challenge_size": 28,
    "counts": {
        "end-to-end": 4,
        "investigator": 8,
        "non-coding": 8,
        "reviewer": 6,
        "test-strategy": 2
    },
    "unique_ids": true,
    "ambiguous_controls": 2
}
```

### Run ablation experiment

```bash
PYTHONPATH=src python3 -m qualification.role_suite.v3.runner run \
  --output ~/.local/share/codex-local-harness/ablation-165/v3 \
  --opencode /path/to/opencode \
  --model deepseek-v4-flash-0731-exl3-k2 \
  --route deepseek-qualified \
  --policy role-contract \
  --policy role-contract-plus-verification \
  --repeat 3
```

**Arguments:**
- `--output`: Output directory for attempts and aggregates
- `--opencode`: OpenCode worker binary path
- `--model`: Model name (e.g., `deepseek-v4-flash-0731-exl3-k2`)
- `--route`: Route name for tracking
- `--policy`: Policy variant(s) to test (repeat for multiple)
- `--challenge-subset`: `all`, `deepseek_misses`, `passing_anchors`, `ambiguous_controls`
- `--case`: Filter by case ID (repeat for multiple)
- `--repeat`: Number of repeats per case/policy (default: 1)
- `--timeout`: Timeout override in seconds
- `--resume`: Resume from existing attempts

### Select challenge subsets

```bash
# Only DeepSeek misses from v2
PYTHONPATH=src python3 -m qualification.role_suite.v3.runner run \
  --output /path \
  --opencode /path/to/opencode \
  --model deepseek-v4-flash \
  --route test \
  --challenge-subset deepseek_misses

# Only passing anchors
PYTHONPATH=src python3 -m qualification.role_suite.v3.runner run \
  --output /path \
  --opencode /path/to/opencode \
  --model deepseek-v4-flash \
  --route test \
  --challenge-subset passing_anchors

# Only ambiguous controls (excluded from promotion scoring)
PYTHONPATH=src python3 -m qualification.role_suite.v3.runner run \
  --output /path \
  --opencode /path/to/opencode \
  --model deepseek-v4-flash \
  --route test \
  --challenge-subset ambiguous_controls
```

## Output Artifacts

Each attempt writes collision-safe artifacts:

```
output/
└── attempts/
    └── <policy_case_repeat_hash>/
        └── <case_id>/
            ├── attempt.json          # Full record with all metadata
            ├── events.jsonl          # Raw worker events
            ├── evidence.json         # Evidence provided to model
            ├── result.json           # Model output
            └── evidence_artifact.json # Evidence without oracle (audit-safe)
```

**Attempt Record Fields:**
```json
{
    "schema_version": 3,
    "case_id": "review-single-06",
    "suite": "reviewer",
    "category": "reviewer",
    "model": "deepseek-v4-flash",
    "route": "deepseek-qualified",
    "policy": "role-contract-plus-verification",
    "policy_category": "role-specific+verification",
    "repeat": 1,
    "identity_hash": "<deterministic_hash>",
    "state": "completed",
    "terminal_completed": true,
    "artifact_correct": true,
    "completed_and_correct": true,
    "elapsed_seconds": 45.234,
    "tool_calls": 3,
    "tool_errors": 0,
    "tokens_total": 1250,
    "worker_exit_code": 0,
    "oracle": {
        "answers": ["RESOURCE_LEAK"],
        "expected_count": 1,
        "workspace_mutated": false
    },
    "observed_at": "2026-09-05T12:34:56.789012+00:00",
    "is_challenge_case": true,
    "is_ambiguous_clean_control": false,
    "is_passing_anchor": false,
    "is_deepseek_miss": true,
    "challenge_set_size": 28
}
```

**Evidence Artifact (audit-safe):**
```json
{
    "case_id": "review-single-06",
    "policy": "role-contract-plus-verification",
    "evidence": {
        "patch": "f = open(path); return f.read()",
        "allowed_findings": ["RESOURCE_LEAK"]
    },
    "observed_at": "2026-09-05T12:34:56.789012+00:00"
}
```

## Aggregation Metrics

Run output includes comprehensive aggregation:

```json
{
    "attempts": 112,
    "artifact_correct": 68,
    "completed_and_correct": 56,
    "timeouts": 0,
    "failures": 56,
    "elapsed_seconds": 1234.567,
    "tool_calls": 224,
    "tool_errors": 2,
    "tokens_total": 280000,
    "category_stats": {
        "reviewer": {"attempts": 48, "correct": 32, "completed": 48, "correct_rate": 0.667},
        "investigator": {"attempts": 24, "correct": 18, "completed": 24, "correct_rate": 0.75},
        ...
    },
    "policy_stats": {
        "structured-v1": {"attempts": 28, "correct": 12, "correct_rate": 0.429},
        "role-contract": {"attempts": 28, "correct": 18, "correct_rate": 0.643},
        "role-contract-plus-verification": {"attempts": 28, "correct": 22, "correct_rate": 0.786},
        "role-contract-plus-uncertainty": {"attempts": 28, "correct": 20, "correct_rate": 0.714}
    },
    "challenge": {
        "total_attempts": 112,
        "challenge_correct": 68,
        "challenge_completed": 112,
        "correct_rate": 0.607
    },
    "promotion_scored": {
        "attempts": 104,
        "promo_correct": 64,
        "promo_completed": 104,
        "correct_rate": 0.615
    },
    "deepseek_miss_recovery": {
        "total_misses": 17,
        "recovered": 12,
        "recovery_rate": 0.706
    },
    "consistency": {
        "total_case_policy_pairs": 112,
        "consistent_pairs": 84,
        "consistency_rate": 0.75
    }
}
```

### Metric Definitions

| Metric | Definition |
|--------|------------|
| `attempts` | Total run attempts |
| `artifact_correct` | Exact-set match with oracle |
| `completed_and_correct` | Artifact correct AND worker completed |
| `timeouts` | Cases exceeding timeout |
| `failures` | Worker exit code non-zero |
| `elapsed_seconds` | Total wall time |
| `tool_calls` | Total tool call events |
| `tool_errors` | Tool call errors |
| `tokens_total` | Total token usage |
| `category_stats` | Per-category breakdown |
| `policy_stats` | Per-policy breakdown |
| `challenge.correct_rate` | Challenge set accuracy |
| `promotion_scored.correct_rate` | Challenge excluding ambiguous controls |
| `deepseek_miss_recovery.recovery_rate` | DeepSeek misses now corrected |
| `consistency.consistency_rate` | Same result across repeats |

## Promotion Gate

**DeepSeek qualification requires:**

1. **Minimum Correctness Threshold**: `promotion_scored.correct_rate >= 0.60`
   - Excludes ambiguous clean controls (01, 04) from scoring
   - Ensures deep, not just surface-level, understanding

2. **DeepSeek Miss Recovery**: `deepseek_miss_recovery.recovery_rate >= 0.65`
   - Policy must fix at least 65% of DeepSeek's v2 structured misses
   - Demonstrates actual improvement over baseline

3. **Consistency**: `consistency.consistency_rate >= 0.70`
   - At least 70% of case/policy combinations produce same result across repeats
   - Ensures deterministic behavior

4. **No Regression**: `artifact_correct >= v2_structured_score` (47/62 = 0.758)
   - Policy must not regress from v2 structured performance
   - Maintains baseline quality

**Example promotion decision:**
```
Policy: role-contract-plus-verification

Metrics:
  - promotion_scored.correct_rate: 0.64 (104 attempts, 64 correct)
  - deepseek_miss_recovery.recovery_rate: 0.71 (17 misses, 12 recovered)
  - consistency.consistency_rate: 0.75 (112 pairs, 84 consistent)
  - artifact_correct: 0.61 (112 attempts, 68 correct) [v2 structured: 0.76]

Decision: REJECTED
  - artifact_correct (0.61) < v2 structured (0.76) - REGRESSION DETECTED
  - All other thresholds met
  - Recommendation: Investigate regression before promotion
```

## Testing

Run unit and integration tests:

```bash
PYTHONPATH=src python3 -m pytest tests/test_role_suite_v3.py -v
```

Test categories:
- `TestChallengeSet`: Challenge subset selection and metadata
- `TestPolicies`: Policy variant definitions and composition
- `TestAggregation`: Metric computation and edge cases
- `TestRunnerIntegration`: Fake-worker execution and artifact validation

## Safety and Privacy

- **Oracle separation**: Evidence artifacts never contain expected/forbidden oracle
- **Identity validation**: Deterministic hash binds public case, private oracle, and policy
- **Collision-safe**: Model/policy/repeat hash prevents artifact overwrites
- **Audit-safe**: Separate evidence artifact with oracle stripped

## Migration from v2

**v2 corpus preserved**: No changes to `qualification/role_suite/v2/`

To run v2 (frozen, unchanged):
```bash
PYTHONPATH=src python3 -m qualification.role_suite.v2.run verify
PYTHONPATH=src python3 -m qualification.role_suite.v2.run run --output /path --opencode /path --model deepseek-v4-flash --route default --policy structured
```

To run v3 (ablation experiment):
```bash
PYTHONPATH=src python3 -m qualification.role_suite.v3.runner verify
PYTHONPATH=src python3 -m qualification.role_suite.v3.runner run --output /path --opencode /path --model deepseek-v4-flash --route deepseek-qualified --policy role-contract-plus-verification --repeat 3
```

## References

- Issue #165: DeepSeek role qualification
- Issue #166: Prompt-ablation experiment
- `qualification/role_suite/v2/RESULTS-issue-165.md`: v2 results
- `qualification/role_suite/v3/challenge_set.py`: Challenge selection logic
- `qualification/role_suite/v3/policies.py`: Policy variant definitions
