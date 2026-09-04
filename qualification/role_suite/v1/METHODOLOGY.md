# Role-Suite v1 Methodology

## Overview

The role-suite v1 framework provides a deterministic, repeatable method for qualifying worker roles in the codex-local-harness. This document describes the methodology, design principles, and usage patterns.

## Design Principles

### 1. Determinism

All fixtures are immutable and use SHA-256 identity hashes for cross-run matching. This ensures:
- Same inputs always produce same outputs
- Results are reproducible across different environments
- Identity matching works reliably between runs

### 2. Isolation

Each test case is self-contained with:
- Its own prompt and context
- Independent oracle checks
- Separate timeout settings
- No shared state between cases

### 3. Extensibility

The framework supports:
- Multiple suite types (reviewer, investigator, test-strategy, non-coding, end-to-end)
- Policy variants (baseline, explicit-stop, structured/phased)
- Custom oracle checks
- Arbitrary OpenCode executables and models

### 4. Composability

Cases can be combined in various ways:
- Run single suites for focused evaluation
- Run multiple suites for comprehensive coverage
- Compare different models or routes against same cases

## Suite Structure

### Reviewer Suite (20 cases)

| Category | Count | Purpose |
|----------|-------|---------|
| Single Defect | 8 | Isolate single bug patterns |
| Interacting | 4 | Detect correlated defects |
| Clean | 4 | Detect false positives |
| Inadequate Tests | 2 | Verify test sufficiency |
| Concurrency/State | 2 | Detect timing-related bugs |

**Rationale**: Reviewer qualification requires detecting real bugs while not flagging clean patches. The distribution ensures balanced coverage of positive and negative cases.

### Investigator Suite (7 categories)

Each category targets a specific analysis type:
- **Behavior Analysis**: Expected vs actual behavior differences
- **Performance Profiling**: Bottleneck identification
- **Log Analysis**: Error pattern detection
- **Memory Analysis**: Leak or allocation patterns
- **Network Behavior**: Protocol or connection issues
- **Resource Contention**: Lock or semaphore problems
- **Edge Case Discovery**: Boundary condition failures

### Test-Strategy Suite (5 categories)

Designed to evaluate test planning capabilities:
- **Coverage Expansion**: Missing test coverage areas
- **Integration Gaps**: Cross-component testing
- **Stress Scenarios**: High-load or high-concurrency tests
- **Security Boundary Tests**: Authorization and validation tests
- **Recovery Tests**: Failure recovery and rollback scenarios

### Non-Coding Suite (24 cases)

Six categories × 4 cases each:
- **Documentation**: README, API docs, examples
- **Configuration**: YAML/JSON config validation
- **Build System**: Makefile, CMake, build scripts
- **CI/CD**: Pipeline configuration and tests
- **Security**: Secret scanning, permission reviews
- **Compliance**: License, copyright, attribution

**Rationale**: Non-coding tasks are common in real workflows. Four cases per category ensures each has sufficient sample size for statistical validity.

### End-to-End Suite (4+ cases)

Complete workflow scenarios:
- Issue creation to resolution
- Branch-based development and PR review
- Hotfix and deployment workflow
- Feature development with test coverage

**Rationale**: End-to-end tests verify the complete workflow integrity, not just isolated tasks.

## Oracle Mechanisms

### External Deterministic Oracles

Oracles verify output against expected criteria:

1. **Defect Count**: `defect_count:N` or `defect_count:>=N`
2. **Analysis Complete**: `analysis_complete:true`
3. **Strategy Complete**: `strategy_complete:true`
4. **Workflow Complete**: `workflow_complete:true`
5. **JSON Schema**: Required field presence
6. **Terminal Format**: Valid JSON structure

### Read-Only Enforcement

Where specified, oracles check that workers only use read-only operations:
- No file writes
- No state modifications
- Analysis-only responses

### False Positive Detection

Clean patches should not produce findings. The oracle:
- Checks if clean patches get flagged (false positive)
- Applies penalties for incorrect findings
- Tracks true positive vs false positive rates

## Policy Variants

### Baseline

Standard evaluation without modifications. Use for:
- Baseline comparison
- Normal operation
- Default qualification runs

### Explicit-Stop

Terminate after first failure for faster iteration. Use for:
- Quick validation
- Debugging specific failures
- Time-constrained environments

### Structured/Phased

Multi-phase evaluation with checkpointing. Use for:
- Complex workflows
- Verification of phase transitions
- Process compliance checking

## Metrics

### Per-Attempt Metrics

- `elapsed_seconds`: Wall-clock duration
- `tool_calls`: Number of tool invocations
- `tool_errors`: Number of tool failures
- `tokens`: Token consumption (local + frontier)
- `corrections`: Number of correction cycles
- `state`: completed, failed, timed-out, etc.

### Aggregate Metrics

- Success rate per case/group
- Mean elapsed time
- Mean token usage
- Mean corrections
- Standard deviation
- Sample size warnings

## Usage Patterns

### Running a Single Suite

```bash
python3 qualification/role-suite/v1/runner.py \
  --manifest qualification/role-suite/v1/manifest.json \
  --suite reviewer \
  --opencode ~/.local/bin/opencode \
  --model vllm/Qwen/Qwen3-Coder-Next-FP8 \
  --route coder/complex \
  --output ~/.local/share/codex-local-harness/role-suite/v1/runs \
  --policy baseline
```

### Running with Resume

```bash
# First run (interrupted)
python3 qualification/role-suite/v1/runner.py \
  --manifest manifest.json \
  --suite reviewer \
  --output runs \
  --resume

# Continue from last state
python3 qualification/role-suite/v1/runner.py \
  --manifest manifest.json \
  --suite reviewer \
  --output runs \
  --resume
```

### Comparing Models

```bash
# Run model A
python3 qualification/role-suite/v1/runner.py \
  --manifest manifest.json \
  --suite reviewer \
  --model vllm/Qwen/Qwen3-Coder-Next-FP8 \
  --output runs/model-a

# Run model B
python3 qualification/role-suite/v1/runner.py \
  --manifest manifest.json \
  --suite reviewer \
  --model vllm/Qwen/Qwen3.8-27B-FP8 \
  --output runs/model-b

# Compare
python3 qualification/role-suite/v1/cli.py compare \
  --left runs/model-a/attempts.jsonl \
  --right runs/model-b/attempts.jsonl \
  --output comparison.jsonl
```

### Aggregating Results

```bash
python3 qualification/role-suite/v1/cli.py aggregate \
  --attempts runs/model-a/attempts.jsonl \
  --output runs/model-a/metrics.jsonl
```

## Quality Gates

### git diff --check

The suite must pass `git diff --check` to ensure no trailing whitespace in any committed file. This is checked before commit.

### Test Coverage

Focused tests verify:
- Case creation and validation
- Metrics computation
- Oracle checks
- Policy configuration
- Manifest persistence

### Determinism Verification

Run the same suite twice and verify:
- Same identity hashes for matching cases
- Consistent metrics (allowing for timing variance)
- Same case selection

## Troubleshooting

### Common Issues

1. **Timeout failures**: Increase `--timeout` or use `explicit-stop` policy
2. **Identity mismatches**: Check that manifest is consistent across runs
3. **False positives**: Review oracle checks and clean patch definitions
4. **Trailing whitespace**: Run `git diff --check` and fix before commit

### Debug Mode

```bash
# Enable verbose logging
export PYTHONPATH=src
python3 -m pytest tests/test_role_suite_v1.py -v -s
```

## Future Extensibility

The framework is designed for extension:

1. Add new suite types by adding to `SUITE_CATEGORIES`
2. Add new oracle checks in `oracles.py`
3. Add new policy variants in `policies.py`
4. Add new categories by extending case creation functions

## References

- Issue #165: Role qualification suite
- v5 runner documentation
- Benchmark module design
- Health metrics schema
