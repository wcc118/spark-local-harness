"""Paired-model benchmark and dashboard comparison foundation."""
from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .health import RunHealth, TokenUsage, append_jsonl
from .diagnostics import Adjudication, LifecycleEvent, append_record

SCHEMA_VERSION = 1

# Known fixture categories covering: investigation, focused fixes, tests, UI, refactoring, diagnosis, hygiene, review
FIXTURE_CATEGORIES = {
    "investigation": "Case study examining behavior or performance characteristics",
    "focused_fix": "Minimal isolated fix addressing a specific defect",
    "tests": "Test suite additions or improvements",
    "ui": "User interface or CLI enhancement",
    "refactoring": "Code structure or organization change",
    "diagnosis": "Diagnostic logging or telemetry improvement",
    "hygiene": "Code quality, formatting, or maintenance task",
    "review": "Review process or verification workflow",
}

# Sample prompts for reproducible fixtures
SAMPLE_PROMPTS = {
    "investigation": "Analyze the current behavior of {function} and document any anomalies found.",
    "focused_fix": "Fix the bug in {function} where {problem} occurs. Ensure {expected} behavior.",
    "tests": "Add unit tests for {function} covering {scenario}. Include edge cases.",
    "ui": "Improve the CLI output of {command} to show {detail} in a user-friendly format.",
    "refactoring": "Refactor {module} to improve {aspect} without changing external behavior.",
    "diagnosis": "Add logging to {component} that records {metric} for debugging purposes.",
    "hygiene": "Format {file} according to project style guidelines and fix lint warnings.",
    "review": "Review {pr} for correctness and suggest improvements to {aspect}.",
}


@dataclass(frozen=True)
class BenchmarkManifest:
    """Versioned manifest for benchmark runs."""
    manifest_id: str
    schema_version: int = SCHEMA_VERSION
    created_at: str = ""
    description: str = ""
    fixtures_count: int = 0

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported manifest schema version")
        if self.fixtures_count < 0:
            raise ValueError("fixtures_count must be non-negative")

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["created_at"] = result.get("created_at") or datetime.now(timezone.utc).isoformat()
        return result

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> BenchmarkManifest:
        return cls(**payload)


@dataclass(frozen=True)
class BenchmarkFixture:
    """Reproducible fixture with identity hash for paired comparisons."""
    fixture_id: str
    category: str
    prompt: str
    context: str
    identity_hash: str
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.category not in FIXTURE_CATEGORIES:
            raise ValueError(f"invalid fixture category: {self.category}")
        if not self.fixture_id or not self.fixture_id.strip():
            raise ValueError("fixture_id must be non-empty")
        if not self.prompt or not self.prompt.strip():
            raise ValueError("prompt must be non-empty")

    @property
    def identity(self) -> str:
        """Return identity string for matching attempts across models."""
        return f"{self.category}:{self.fixture_id}"

    @classmethod
    def compute_hash(cls, content: str) -> str:
        """Compute deterministic SHA-256 hash for content."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def create(cls, fixture_id: str, category: str, prompt: str, context: str, **metadata: Any) -> BenchmarkFixture:
        """Create a fixture with computed identity hash."""
        content = f"{category}:{fixture_id}:{prompt}"
        identity_hash = cls.compute_hash(content)
        return cls(
            fixture_id=fixture_id,
            category=category,
            prompt=prompt,
            context=context,
            identity_hash=identity_hash,
            metadata=metadata or {},
        )


@dataclass(frozen=True)
class BenchmarkAttempt:
    """Single run attempt with identity for paired matching."""
    attempt_id: str
    identity_hash: str  # Matches BenchmarkFixture.identity_hash
    model: str
    route: str
    fixture_id: str
    category: str
    elapsed_seconds: float
    state: str  # completed, failed, interrupted, cancelled, timed-out
    tokens: TokenUsage | None
    tool_calls: int
    tool_errors: int
    corrections: int | None
    permissions_requested: int
    questions_asked: int
    interventions: int
    finding_refs: tuple[str, ...]
    run_id: str
    observed_at: str
    deterministic_correct: bool | None = None
    raw_events: str | None = None

    def __post_init__(self) -> None:
        if self.category not in FIXTURE_CATEGORIES:
            raise ValueError(f"invalid category: {self.category}")
        # Allow both standard states and status values from RunHealth
        valid_states = {"completed", "failed", "interrupted", "cancelled", "timed-out"}
        status_map = {"success": "completed", "failure": "failed"}
        state_to_check = status_map.get(self.state, self.state)
        if state_to_check not in valid_states:
            raise ValueError(f"invalid state: {self.state}")
        if self.corrections is not None and self.corrections < 0:
            raise ValueError("corrections must be non-negative")
        if self.permissions_requested < 0:
            raise ValueError("permissions_requested must be non-negative")
        if self.questions_asked < 0:
            raise ValueError("questions_asked must be non-negative")
        if self.interventions < 0:
            raise ValueError("interventions must be non-negative")
        if self.tool_errors > self.tool_calls:
            raise ValueError("tool_errors cannot exceed tool_calls")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_run_health(cls, run: RunHealth, model: str, route: str, fixture_id: str, category: str) -> BenchmarkAttempt:
        """Create attempt from existing RunHealth record."""
        # Map RunHealth status to standard benchmark states
        status_to_state = {
            "success": "completed",
            "failure": "failed",
        }
        state = status_to_state.get(run.status, run.status)

        return cls(
            attempt_id=run.run_id,
            identity_hash=BenchmarkFixture.compute_hash(f"{category}:{fixture_id}:{run.task_class}"),
            model=model,
            route=route,
            fixture_id=fixture_id,
            category=category,
            elapsed_seconds=run.wall_seconds,
            state=state,
            tokens=run.usage,
            tool_calls=run.tool_calls,
            tool_errors=run.tool_errors,
            corrections=run.correction_cycle,
            permissions_requested=0,  # Would require control plane data
            questions_asked=0,  # Would require control plane data
            interventions=1 if run.intervention_depth != "none" else 0,
            finding_refs=(),
            run_id=run.run_id,
            observed_at=datetime.now(timezone.utc).isoformat(),
        )


@dataclass(frozen=True)
class PairedSummary:
    """Summary comparing paired attempts on the same fixture."""
    summary_id: str
    fixture_id: str
    category: str
    left_attempt_id: str
    right_attempt_id: str
    left_model: str
    right_model: str
    left_route: str
    right_route: str
    left_tokens: int | None
    right_tokens: int | None
    left_elapsed: float | None
    right_elapsed: float | None
    left_correct: bool | None
    right_correct: bool | None
    correct_first: bool | None
    tie: bool
    inconclusive: bool
    token_reduction: float | None
    time_reduction: float | None
    notes: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BenchmarkMetrics:
    """Aggregated metrics for benchmark analysis."""
    model: str
    route: str
    category: str
    runs: int
    success_rate: float | None
    mean_elapsed: float | None
    mean_tokens: float | None
    mean_corrections: float | None
    mean_tool_errors: float | None
    sample_warning: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
