"""Versioned frozen run manifest/config for benchmark evaluation.

This module defines:
- Model names and routes
- Policy variants and selection rules
- Repetitions configuration
- Serving expectations
- Timeout rules
- Corpus versions
- Output location

Policy selection must occur on declared development/challenge evidence;
full confirmation results must not silently tune policy.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ModelConfig:
    """Configuration for a single model."""
    name: str
    route: str
    tier: str  # production, experimental, diagnostic
    baseline: bool  # Is this the baseline for comparison?

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PolicyConfig:
    """Configuration for a policy variant."""
    name: str
    category: str  # baseline, role-specific, role-specific+verification, role-specific+uncertainty
    instruction_prefix: str | None  # Path to instruction prefix file
    default: bool  # Is this the default policy?
    description: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RunConfig:
    """Full run configuration with versioning."""
    schema_version: int
    config_id: str
    created_at: str
    models: list[ModelConfig]
    policies: list[PolicyConfig]
    repetitions: int
    selection_rules: dict[str, Any]
    serving_expectations: dict[str, Any]
    timeout_rules: dict[str, Any]
    corpus_versions: dict[str, str]
    output_location: str
    policy_selection_requires_evidence: bool  # Must have evidence for policy changes
    confirmations_require_full_evidence: bool  # Full confirmation must not tune policy

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema version: {self.schema_version}")
        if not self.config_id or not self.config_id.strip():
            raise ValueError("config_id must be non-empty")
        if not self.models:
            raise ValueError("at least one model must be configured")
        if not self.policies:
            raise ValueError("at least one policy must be configured")
        if self.repetitions < 1:
            raise ValueError("repetitions must be at least 1")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RunConfig:
        return cls(**payload)

    def compute_hash(self) -> str:
        """Compute deterministic hash for this configuration."""
        content = {
            "config_id": self.config_id,
            "models": sorted([m.name for m in self.models]),
            "policies": sorted([p.name for p in self.policies]),
            "repetitions": self.repetitions,
            "corpus_versions": dict(sorted(self.corpus_versions.items())),
            "timeout_rules": dict(sorted(self.timeout_rules.items())),
        }
        return hashlib.sha256(
            json.dumps(content, sort_keys=True).encode()
        ).hexdigest()


# Default configurations

DEFAULT_MODELS = [
    ModelConfig(
        name="vllm/Qwen/Qwen3-Coder-Next-FP8",
        route="coder/complex",
        tier="production",
        baseline=True,
    ),
    ModelConfig(
        name="vllm/Qwen/Qwen3.8-27B-FP8",
        route="experimental/qwen38",
        tier="experimental",
        baseline=False,
    ),
    ModelConfig(
        name="deepseek-v4-flash-0731-exl3-k2",
        route="deepseek-qualified",
        tier="production",
        baseline=False,
    ),
]

DEFAULT_POLICIES = [
    PolicyConfig(
        name="baseline-production",
        category="baseline",
        instruction_prefix="qualification/paired-model/policies/compact-v1.md",
        default=True,
        description="Standard production contract: minimal instructions, no explicit phases",
    ),
    PolicyConfig(
        name="structured-inspect-implement-verify",
        category="role-specific",
        instruction_prefix="qualification/paired-model/policies/structured-phased.md",
        default=False,
        description="4-phase structured contract: Inspect, Implement, Verify, Deliver",
    ),
    PolicyConfig(
        name="acceptance-criteria-test-oriented",
        category="role-specific",
        instruction_prefix=None,  # Use v3 role-contract policy
        default=False,
        description="Test-driven contract: acceptance criteria come first, then implementation",
    ),
    PolicyConfig(
        name="verification-plus-uncertainty-escalation",
        category="role-specific+uncertainty",
        instruction_prefix=None,  # Use v3 role-contract-plus-uncertainty policy
        default=False,
        description="Verification-focused contract with explicit uncertainty assessment",
    ),
]

DEFAULT_CORPUS_VERSIONS = {
    "paired_model": "v1.0.0",
    "role_suite": "v3.0.0",
    "role_suite_challenge": "28 cases",
    "role_suite_full": "62 cases",
}

DEFAULT_TIMEOUT_RULES = {
    "default_seconds": 180,
    "concurrency_seconds": 240,
    "enforce_wall_time": True,
    "partial_result_after_timeout": False,  # A successful oracle after timeout is not clean completion
}

DEFAULT_SERVING_EXPECTATIONS = {
    "min_correct_rate": 0.80,
    "max_timeout_rate": 0.05,
    "max_error_rate": 0.02,
    "expected_latency_p95": 60.0,
}


def create_run_config(
    config_id: str,
    models: list[ModelConfig] | None = None,
    policies: list[PolicyConfig] | None = None,
    repetitions: int = 1,
    output_location: str = "~/.local/share/codex-local-harness/benchmark",
) -> RunConfig:
    """Create a new run configuration.

    Args:
        config_id: Unique identifier for this configuration
        models: List of model configurations
        policies: List of policy configurations
        repetitions: Number of repetitions per case
        output_location: Path for output artifacts

    Returns:
        Configured RunConfig
    """
    return RunConfig(
        schema_version=SCHEMA_VERSION,
        config_id=config_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        models=models or DEFAULT_MODELS.copy(),
        policies=policies or DEFAULT_POLICIES.copy(),
        repetitions=repetitions,
        selection_rules={
            "correctness_first": True,
            "non_regressing": True,
            "minimum_correct_rate": 0.80,
        },
        serving_expectations=DEFAULT_SERVING_EXPECTATIONS.copy(),
        timeout_rules=DEFAULT_TIMEOUT_RULES.copy(),
        corpus_versions=DEFAULT_CORPUS_VERSIONS.copy(),
        output_location=output_location,
        policy_selection_requires_evidence=True,
        confirmations_require_full_evidence=True,
    )


def load_run_config(path: Path) -> RunConfig:
    """Load a run configuration from JSON file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema version: {data.get('schema_version')}")
    return RunConfig.from_dict(data)


def save_run_config(config: RunConfig, path: Path) -> None:
    """Save a run configuration to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


# Predefined configurations for common use cases

CONFIG_CODING_BENCHMARK = create_run_config(
    config_id="coding-benchmark-v1",
    repetitions=1,
)

CONFIG_SPECIALIST_BENCHMARK = create_run_config(
    config_id="specialist-benchmark-v1",
    policies=[
        p for p in DEFAULT_POLICIES
        if p.name in ("structured-inspect-implement-verify", "acceptance-criteria-test-oriented")
    ],
    repetitions=3,  # DeepSeek needs more repetitions for consistency
)

CONFIG_LIFECYCLE_BENCHMARK = create_run_config(
    config_id="lifecycle-benchmark-v1",
    policies=[DEFAULT_POLICIES[0]],  # Use baseline for lifecycle
    repetitions=1,
)

CONFIG_FULL_EVALUATION = create_run_config(
    config_id="full-evaluation-v1",
    repetitions=1,
)


def get_config_for_corpus(corpus: str) -> RunConfig:
    """Get default configuration for a specific corpus.

    Args:
        corpus: Corpus name (coding, specialist, lifecycle, full)

    Returns:
        Appropriate RunConfig
    """
    if corpus == "coding":
        return CONFIG_CODING_BENCHMARK
    elif corpus == "specialist":
        return CONFIG_SPECIALIST_BENCHMARK
    elif corpus == "lifecycle":
        return CONFIG_LIFECYCLE_BENCHMARK
    elif corpus == "full":
        return CONFIG_FULL_EVALUATION
    else:
        raise ValueError(f"unknown corpus: {corpus}")
