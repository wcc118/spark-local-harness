# Paired-Model Benchmark Methodology

This document describes the exact commands and workflows for running the paired-model benchmark with issue-165 enhancements.

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    Enhanced Benchmark Architecture                           │
├──────────────────────────────────────────────────────────────────────────────┤
│  • Structured Issue Packets: 10 cases with deterministic identity           │
│  • Coding Contracts: 4 variants for fair comparison                         │
│  • Enhanced Runner: Repetitions, timeout enforcement, metrics               │
│  • Orchestration: Unified entry for coding + specialist + lifecycle         │
│  • Scorecard: Holistic scoring with fail-closed behavior                    │
└──────────────────────────────────────────────────────────────────────────────┘
```

## Core Components

### 1. Issue Packets

Each paired-model case has a structured issue packet with:
- **Objective**: Primary goal statement
- **Context**: Background and constraints
- **Scope**: In-scope items
- **Acceptance Criteria**: Testable success criteria
- **Constraints/Exclusions**: Out-of-scope items
- **Required Verification**: How correctness is determined
- **Durable Deliverable**: Expected output artifact
- **Escalation Conditions**: When to escalate to Codex

Packets are model-independent and identical for the same case/policy comparison.

### 2. Coding Contracts

Four policy variants for fair comparison:

| Contract | Category | Description |
|----------|----------|-------------|
| `baseline-production` | baseline | Standard production contract: minimal instructions |
| `structured-inspect-implement-verify` | role-specific | 4-phase structured: Inspect, Implement, Verify, Deliver |
| `acceptance-criteria-test-oriented` | role-specific | Test-driven: acceptance criteria first, then implementation |
| `verification-plus-uncertainty-escalation` | role-specific+uncertainty | Verification-focused with explicit uncertainty assessment |

### 3. Enhanced Runner

Features:
- **Repetitions**: Run same case multiple times for consistency
- **Enforced Timeout**: Wall-time enforcement in subprocess code
- **Attempt Artifacts**: Identity-validated with oracle separation
- **Metrics**: Correctness, completion, timeout, tool/error, elapsed, tokens
- **Identity Binding**: Full binding of instruction-prefix contents

### 4. Orchestration

Unified entry point for complete evaluation:
- **Coding**: 10 paired-model cases under named coder contracts
- **Specialist**: 62 role-suite cases under v3 contracts
- **Managed Lifecycle**: Frozen set for issue → finding → routing → decision

### 5. Run Config

Versioned frozen config with:
- Model names and routes
- Policy variants and selection rules
- Repetitions configuration
- Serving expectations
- Timeout rules
- Corpus versions
- Output location

## CLI Usage

### Canary Run (Quick Verification)

```bash
PYTHONPATH=src python3 -m codex_local_harness.benchmark_cli canary \
  --output ~/.local/share/codex-local-harness/benchmark-canary \
  --model vllm/Qwen/Qwen3-Coder-Next-FP8 \
  --route coder/complex
```

### Development/Policy Runs

```bash
# Run with baseline policy
PYTHONPATH=src python3 -m codex_local_harness.benchmark_cli run \
  --output ~/.local/share/codex-local-harness/benchmark-dev \
  --model vllm/Qwen/Qwen3-Coder-Next-FP8 \
  --route coder/complex \
  --policy baseline-production \
  --case focused-fix
```

### Confirmation Runs

```bash
# Run with multiple policies for comparison
PYTHONPATH=src python3 -m codex_local_harness.benchmark_cli run \
  --output ~/.local/share/codex-local-harness/benchmark-confirm \
  --model vllm/Qwen/Qwen3-Coder-Next-FP8 \
  --route coder/complex \
  --policy baseline-production \
  --policy structured-inspect-implement-verify \
  --repeat 3 \
  --resume
```

### Aggregation and Scorecard

```bash
# Generate scorecard from completed runs
PYTHONPATH=src python3 -m codex_local_harness.benchmark_cli scorecard \
  --output ~/.local/share/codex-local-harness/benchmark-confirm
```

### Resume from Previous Runs

```bash
PYTHONPATH=src python3 -m codex_local_harness.benchmark_cli run \
  --output ~/.local/share/codex-local-harness/benchmark \
  --model vllm/Qwen/Qwen3-Coder-Next-FP8 \
  --route coder/complex \
  --resume
```

### Both-Model Execution

```bash
# Run first model
PYTHONPATH=src python3 -m codex_local_harness.benchmark_cli run \
  --output ~/.local/share/codex-local-harness/benchmark \
  --model vllm/Qwen/Qwen3-Coder-Next-FP8 \
  --route coder/complex

# Run second model
PYTHONPATH=src python3 -m codex_local_harness.benchmark_cli run \
  --output ~/.local/share/codex-local-harness/benchmark \
  --model vllm/Qwen/Qwen3.8-27B-FP8 \
  --route experimental/qwen38

# Compare results
PYTHONPATH=src python3 -m codex_local_harness.benchmark_cli compare \
  --output ~/.local/share/codex-local-harness/benchmark \
  --left vllm/Qwen/Qwen3-Coder-Next-FP8 \
  --right vllm/Qwen/Qwen3.8-27B-FP8
```

## Output Artifacts

Each attempt writes collision-safe artifacts:

```
output/
├── attempts/
│   └── <model_policy_hash>/
│       └── <case_id>/
│           ├── attempt.json          # Full record with all metrics
│           ├── events.jsonl          # Raw worker events
│           ├── workspace/            # Isolated workspace
│           └── identity.txt          # Identity hash for verification
├── benchmark-results.json           # Aggregated results
├── scorecard.json                   # JSON scorecard
├── scorecard.md                     # Markdown scorecard
└── run-config.json                  # Versioned run configuration
```

## Identity Binding

The identity hash binds:
1. **Instruction prefix contents** (fixed mismatched resume issue)
2. **Model name**
3. **Policy variant**
4. **Case-specific inputs** (source hash, task hash, check hash)
5. **Permission profile**

This ensures that any change to these fields produces a different identity hash.

## Timeout Semantics

- **Enforced wall-time timeout** in subprocess code
- **Prompt-only limits are NOT enforcement**
- **A successful oracle after timeout is recorded as `state='timed-out'`**
- **Not considered `state='completed'`**

## Scorecard Behavior

- **Correctness is primary**: 60% weight
- **Completion is secondary**: 20% weight
- **Time efficiency**: 20% weight
- **Fail closed**: Missing/incomplete cohorts get 0.0 score
- **Estimated displacement**: Explicit assumptions (not fabricated)

## Test Categories

New tests cover:
- Packet rendering and identity binding
- Corpus shapes and policy selection
- Aggregation and lifecycle scoring
- Timeout semantics
- Deterministic report generation
- Fake-worker execution without artifact leakage

Run tests:
```bash
PYTHONPATH=src python3 -m pytest tests/test_issue_packet.py -v
```

## Safety

- Raw generated attempts remain outside git
- No real GitHub mutations in benchmark cases
- Oracle separated from worker workspace
- Deterministic execution (no timing-dependent results)
