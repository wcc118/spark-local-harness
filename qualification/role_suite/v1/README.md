# Role-Suite v1 Framework

Self-contained qualification suite for role-based worker evaluation.

## Overview

This suite provides:

- **Named suites/policy variants**: Selectable by name with configuration-driven behavior
- **Selected cases**: Fixed subset of test cases for repeatable evaluation
- **Repeat numbers**: Configurable trial repetitions per case
- **Timeout overrides**: Per-case or global timeout adjustments
- **Resume capability**: Track completion state to skip already-run cases
- **Collision-free attempts**: Deterministic attempt IDs based on case+seed+repeat

## Artifact Types

Each suite produces:

- **Correctness artifact**: Whether output matches oracle expectations
- **Terminal completion artifact**: Whether run terminated (not hung)
- **Completed-and-correct artifact**: Intersection of correctness and completion
- **Elapsed/tool/token metrics**: Timing, tool usage, token consumption
- **Immutable matched identities**: SHA-256 hash of case definition for cross-run matching

## Fixtures

### Reviewer Cases (20 total)

| Category | Count | Description |
|----------|-------|-------------|
| Single defects | 8 | Isolated single-problem patches |
| Interacting | 4 | Multiple defects interacting |
| Clean | 4 | Correct patches with no defects |
| Inadequate tests | 2 | Patches with missing or inadequate tests |
| Concurrency/state | 2 | Concurrency bugs and state-related issues |

### Investigator Cases (7 categories)

1. **Behavior analysis**: Expected vs actual behavior differences
2. **Performance profiling**: Bottleneck identification
3. **Log analysis**: Error pattern detection
4. **Memory analysis**: Leak or allocation patterns
5. **Network behavior**: Protocol or connection issues
6. **Resource contention**: Lock or semaphore problems
7. **Edge case discovery**: Boundary condition failures

### Test-Strategy Cases (5 categories)

1. **Coverage expansion**: Missing test coverage areas
2. **Integration gaps**: Cross-component testing
3. **Stress scenarios**: High-load or high-concurrency tests
4. **Security boundary tests**: Authorization and validation tests
5. **Recovery tests**: Failure recovery and rollback scenarios

### Non-Coding Cases (6 categories × 4 cases = 24 total)

1. **Documentation**: README, API docs, examples
2. **Configuration**: YAML/JSON config validation and updates
3. **Build system**: Makefile, CMake, build script fixes
4. **CI/CD**: Pipeline configuration and test fixes
5. **Security**: Secret scanning, permission reviews
6. **Compliance**: License, copyright, attribution

### End-to-End Cases (minimum 4)

Complete workflow scenarios from issue creation through validation.

## Oracle Mechanisms

External deterministic oracles verify:

- Output schema compliance
- Required field presence
- Valid enum values
- Path relative-to-repository constraints
- Token budget compliance
- Tool call limits

## Policies

- **baseline**: Standard evaluation without modifications
- **explicit-stop**: Terminate after first failure for faster iteration
- **structured/phased**: Multi-phase evaluation with checkpointing

## Usage

```bash
python3 qualification/role-suite/v1/run.py \
  --suite reviewer \
  --case 1 \
  --repeat 3 \
  --timeout 300 \
  --opencode ~/.local/bin/opencode \
  --model vllm/Qwen/Qwen3-Coder-Next-FP8 \
  --route coder/complex
```

## Metrics

Per-attempt:

- elapsed_seconds
- tool_calls
- tool_errors
- tokens (local_input, local_output, local_reasoning, frontier_input, frontier_output, frontier_reasoning)
- corrections

Aggregate:

- success_rate
- mean_elapsed, mean_tokens, mean_corrections
- standard deviation and sample size warnings
