"""Role-suite v1 framework for worker qualification."""
from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

# Use absolute imports for compatibility with importlib
try:
    from codex_local_harness.health import RunHealth, TokenUsage
    from codex_local_harness.benchmark import BenchmarkFixture, FIXTURE_CATEGORIES
except ImportError:
    # Fallback for when run as standalone
    RunHealth = None  # type: ignore
    TokenUsage = None  # type: ignore
    BenchmarkFixture = None  # type: ignore
    FIXTURE_CATEGORIES = {}


SCHEMA_VERSION = 1

# Role types for qualification
ROLE_TYPES = {
    "reviewer": "Patch review with defect detection",
    "investigator": "Behavior and problem analysis",
    "test-strategy": "Test planning and coverage",
    "non-coding": "Documentation, config, build, CI, security, compliance",
    "end-to-end": "End-to-end workflow scenarios",
}

# Suite categories
SUITE_CATEGORIES = {
    "reviewer": "reviewer",
    "investigator": "investigator",
    "test-strategy": "test-strategy",
    "non-coding": "non-coding",
    "end-to-end": "end-to-end",
}

# Reviewer subcategories
REVIEWER_CATEGORIES = {
    "single_defect": "Single isolated defect",
    "interacting": "Multiple interacting defects",
    "clean": "No defects",
    "inadequate_tests": "Missing or inadequate tests",
    "concurrency_state": "Concurrency or state-related issues",
}

# Investigator categories
INVESTIGATOR_CATEGORIES = {
    "behavior_analysis": "Expected vs actual behavior differences",
    "performance_profiling": "Bottleneck identification",
    "log_analysis": "Error pattern detection",
    "memory_analysis": "Leak or allocation patterns",
    "network_behavior": "Protocol or connection issues",
    "resource_contention": "Lock or semaphore problems",
    "edge_case_discovery": "Boundary condition failures",
}

# Test-strategy categories
TEST_STRATEGY_CATEGORIES = {
    "coverage_expansion": "Missing test coverage areas",
    "integration_gaps": "Cross-component testing",
    "stress_scenarios": "High-load or high-concurrency tests",
    "security_boundary_tests": "Authorization and validation tests",
    "recovery_tests": "Failure recovery and rollback scenarios",
}

# Non-coding categories
NON_CODING_CATEGORIES = {
    "documentation": "README, API docs, examples",
    "configuration": "YAML/JSON config validation and updates",
    "build_system": "Makefile, CMake, build script fixes",
    "ci_cd": "Pipeline configuration and test fixes",
    "security": "Secret scanning, permission reviews",
    "compliance": "License, copyright, attribution",
}

# Policy variants
POLICY_VARIANTS = {
    "baseline": "Standard evaluation without modifications",
    "explicit-stop": "Terminate after first failure for faster iteration",
    "structured/phased": "Multi-phase evaluation with checkpointing",
}


@dataclass(frozen=True)
class SuiteCase:
    """A single test case in the suite."""
    case_id: str
    suite: str
    category: str
    prompt: str
    context: str
    oracle_check: str  # Path or inline check specification
    timeout_seconds: int | None = None
    expected_tools: int | None = None
    expected_tokens: int | None = None
    repeat: int = 1
    identity_hash: str = ""

    def __post_init__(self) -> None:
        if self.suite not in SUITE_CATEGORIES:
            raise ValueError(f"invalid suite: {self.suite}")
        if not self.case_id or not self.case_id.strip():
            raise ValueError("case_id must be non-empty")
        if not self.prompt or not self.prompt.strip():
            raise ValueError("prompt must be non-empty")

    @property
    def identity(self) -> str:
        """Return identity string for matching across runs."""
        return f"{self.suite}:{self.category}:{self.case_id}"

    def as_dict(self) -> dict[str, Any]:
        """Return dict representation for serialization."""
        return {
            "case_id": self.case_id,
            "suite": self.suite,
            "category": self.category,
            "prompt": self.prompt,
            "context": self.context,
            "oracle_check": self.oracle_check,
            "timeout_seconds": self.timeout_seconds,
            "repeat": self.repeat,
            "identity_hash": self.identity_hash,
        }

    @classmethod
    def compute_hash(cls, content: str) -> str:
        """Compute deterministic SHA-256 hash for content."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    @classmethod
    def create(
        cls,
        case_id: str,
        suite: str,
        category: str,
        prompt: str,
        context: str,
        oracle_check: str = "",
        timeout_seconds: int | None = None,
        repeat: int = 1,
    ) -> SuiteCase:
        """Create a case with computed identity hash."""
        content = f"{suite}:{category}:{case_id}:{prompt}"
        identity_hash = cls.compute_hash(content)
        return cls(
            case_id=case_id,
            suite=suite,
            category=category,
            prompt=prompt,
            context=context,
            oracle_check=oracle_check,
            timeout_seconds=timeout_seconds,
            repeat=repeat,
            identity_hash=identity_hash,
        )


@dataclass(frozen=True)
class SuiteAttempt:
    """Single run attempt with identity for matching."""
    attempt_id: str
    case_id: str
    suite: str
    category: str
    repeat_index: int
    model: str
    route: str
    policy: str
    elapsed_seconds: float
    state: str  # completed, failed, interrupted, cancelled, timed-out
    tokens: TokenUsage | None
    tool_calls: int
    tool_errors: int
    corrections: int | None
    deterministic_correct: bool | None = None
    oracle_match: bool | None = None
    run_id: str = ""
    observed_at: str = ""

    def __post_init__(self) -> None:
        valid_states = {"completed", "failed", "interrupted", "cancelled", "timed-out"}
        if self.state not in valid_states:
            raise ValueError(f"invalid state: {self.state}")
        if self.corrections is not None and self.corrections < 0:
            raise ValueError("corrections must be non-negative")

    @classmethod
    def from_run_health(
        cls,
        run: RunHealth,
        suite: str,
        category: str,
        case_id: str,
        repeat_index: int,
        policy: str,
        model: str,
        route: str,
    ) -> SuiteAttempt:
        """Create attempt from existing RunHealth record."""
        status_to_state = {
            "success": "completed",
            "failure": "failed",
        }
        state = status_to_state.get(run.status, run.status)

        return cls(
            attempt_id=run.run_id,
            case_id=case_id,
            suite=suite,
            category=category,
            repeat_index=repeat_index,
            model=model,
            route=route,
            policy=policy,
            elapsed_seconds=run.wall_seconds,
            state=state,
            tokens=run.usage,
            tool_calls=run.tool_calls,
            tool_errors=run.tool_errors,
            corrections=run.correction_cycle,
            deterministic_correct=run.status == "success",
            oracle_match=None,  # Would require oracle data
            run_id=run.run_id,
            observed_at=datetime.now(timezone.utc).isoformat(),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SuiteSummary:
    """Summary for a suite."""
    suite_id: str
    suite: str
    category: str
    case_id: str
    runs: int
    completed_runs: int
    success_rate: float | None
    mean_elapsed: float | None
    mean_tokens: float | None
    mean_corrections: float | None
    mean_tool_errors: float | None
    sample_warning: str | None
    observed_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["observed_at"] = result.get("observed_at") or datetime.now(timezone.utc).isoformat()
        return result


def create_reviewer_cases() -> list[SuiteCase]:
    """Create the 20 reviewer cases."""
    cases = []

    # Single defects (8 cases)
    for i in range(1, 9):
        cases.append(SuiteCase.create(
            case_id=f"reviewer-sd-{i:02d}",
            suite="reviewer",
            category="single_defect",
            prompt=f"Review patch {i} for single defect patterns.",
            context="Patch contains exactly one defect in a single file.",
            oracle_check="defect_count:1",
            timeout_seconds=300,
            repeat=3,
        ))

    # Interacting defects (4 cases)
    for i in range(1, 5):
        cases.append(SuiteCase.create(
            case_id=f"reviewer-int-{i:02d}",
            suite="reviewer",
            category="interacting",
            prompt=f"Review patch {i} for interacting defects.",
            context="Patch contains multiple defects that interact.",
            oracle_check="defect_count:>=2",
            timeout_seconds=300,
            repeat=3,
        ))

    # Clean patches (4 cases)
    for i in range(1, 5):
        cases.append(SuiteCase.create(
            case_id=f"reviewer-clean-{i:02d}",
            suite="reviewer",
            category="clean",
            prompt=f"Review clean patch {i} with no defects.",
            context="Patch is correct with no defects.",
            oracle_check="defect_count:0",
            timeout_seconds=300,
            repeat=3,
        ))

    # Inadequate tests (2 cases)
    for i in range(1, 3):
        cases.append(SuiteCase.create(
            case_id=f"reviewer-ittest-{i:02d}",
            suite="reviewer",
            category="inadequate_tests",
            prompt=f"Review patch {i} for test adequacy issues.",
            context="Patch modifies functionality but tests are insufficient.",
            oracle_check="test_inadequate:true",
            timeout_seconds=300,
            repeat=3,
        ))

    # Concurrency/state (2 cases)
    for i in range(1, 3):
        cases.append(SuiteCase.create(
            case_id=f"reviewer-conc-{i:02d}",
            suite="reviewer",
            category="concurrency_state",
            prompt=f"Review patch {i} for concurrency or state issues.",
            context="Patch introduces concurrency or state management issues.",
            oracle_check="concurrency_bug:true",
            timeout_seconds=300,
            repeat=3,
        ))

    return cases


def create_investigator_cases() -> list[SuiteCase]:
    """Create the 7 investigator category cases."""
    cases = []

    for cat_id, desc in INVESTIGATOR_CATEGORIES.items():
        # One case per category for initial qualification
        cases.append(SuiteCase.create(
            case_id=f"investigator-{cat_id}-001",
            suite="investigator",
            category=cat_id,
            prompt=f"Analyze behavior and identify {desc.replace(' Analysis', '')} patterns.",
            context="Provide analysis report with findings and recommendations.",
            oracle_check="analysis_complete:true",
            timeout_seconds=45,
            repeat=2,
        ))

    return cases


def create_test_strategy_cases() -> list[SuiteCase]:
    """Create the 5 test-strategy category cases."""
    cases = []

    for cat_id, desc in TEST_STRATEGY_CATEGORIES.items():
        cases.append(SuiteCase.create(
            case_id=f"test-strat-{cat_id}-001",
            suite="test-strategy",
            category=cat_id,
            prompt=f"Design test strategy for {desc.replace(' tests', '')}.",
            context="Provide test plan with coverage areas and edge cases.",
            oracle_check="strategy_complete:true",
            timeout_seconds=45,
            repeat=2,
        ))

    return cases


def create_non_coding_cases() -> list[SuiteCase]:
    """Create 4 cases per non-coding category (24 total)."""
    cases = []

    for cat_id, desc in NON_CODING_CATEGORIES.items():
        for i in range(1, 5):
            cases.append(SuiteCase.create(
                case_id=f"noncoding-{cat_id}-{i:02d}",
                suite="non-coding",
                category=cat_id,
                prompt=f"Review {desc.lower()} for {cat_id.replace('_', ' ')} issues.",
                context="Provide review findings and suggested fixes.",
                oracle_check="review_complete:true",
                timeout_seconds=300,
                repeat=2,
            ))

    return cases


def create_end_to_end_cases() -> list[SuiteCase]:
    """Create at least 4 end-to-end workflow cases."""
    cases = []

    e2e_scenarios = [
        ("issue-creation", "Full workflow from issue creation to resolution"),
        ("branch-workflow", "Branch-based development and PR review"),
        ("hotfix", "Emergency fix and deployment workflow"),
        ("feature-branch", "Feature development with test coverage"),
    ]

    for scenario_id, description in e2e_scenarios:
        cases.append(SuiteCase.create(
            case_id=f"e2e-{scenario_id}-001",
            suite="end-to-end",
            category="workflow",
            prompt=f"Execute {description}.",
            context="Complete all phases: planning, implementation, review, validation.",
            oracle_check="workflow_complete:true",
            timeout_seconds=600,
            repeat=2,
        ))

    return cases


def compute_suite_metrics(attempts: Iterable[SuiteAttempt]) -> list[SuiteSummary]:
    """Compute aggregate metrics for suites."""
    groups: dict[tuple[str, str, str], list[SuiteAttempt]] = {}

    for attempt in attempts:
        key = (attempt.suite, attempt.category, attempt.case_id)
        groups.setdefault(key, []).append(attempt)

    summaries = []
    for (suite, category, case_id), group in groups.items():
        runs = len(group)
        completed = sum(1 for a in group if a.state == "completed")

        elapsed_values = [a.elapsed_seconds for a in group if a.state == "completed"]
        token_values = [a.tokens.total for a in group if a.tokens and a.tokens.total > 0]
        correction_values = [a.corrections for a in group]
        error_values = [a.tool_errors for a in group]

        success_rate = completed / runs if runs > 0 else None
        mean_elapsed = statistics.mean(elapsed_values) if elapsed_values else None
        mean_tokens = statistics.mean(token_values) if token_values else None
        mean_corrections = statistics.mean(correction_values) if correction_values else None
        mean_tool_errors = statistics.mean(error_values) if error_values else None

        sample_warning = None
        if runs < 2:
            sample_warning = f"sample size ({runs}) below recommended minimum (2)"

        summaries.append(SuiteSummary(
            suite_id=f"{suite}-{category}-{case_id}",
            suite=suite,
            category=category,
            case_id=case_id,
            runs=runs,
            completed_runs=completed,
            success_rate=success_rate,
            mean_elapsed=mean_elapsed,
            mean_tokens=mean_tokens,
            mean_corrections=mean_corrections,
            mean_tool_errors=mean_tool_errors,
            sample_warning=sample_warning,
        ))

    return summaries


def create_manifest(suites: list[str] | None = None) -> dict[str, Any]:
    """Create a benchmark manifest for role-suite v1."""
    if suites is None:
        suites = ["reviewer", "investigator", "test-strategy", "non-coding", "end-to-end"]

    cases: list[SuiteCase] = []
    if "reviewer" in suites:
        cases.extend(create_reviewer_cases())
    if "investigator" in suites:
        cases.extend(create_investigator_cases())
    if "test-strategy" in suites:
        cases.extend(create_test_strategy_cases())
    if "non-coding" in suites:
        cases.extend(create_non_coding_cases())
    if "end-to-end" in suites:
        cases.extend(create_end_to_end_cases())

    return {
        "schema_version": SCHEMA_VERSION,
        "manifest_id": "role-suite-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "description": "Role-based qualification suite for worker evaluation",
        "suites": suites,
        "cases_count": len(cases),
        "cases": [c.as_dict() for c in cases],
    }


def load_manifest(path: Path) -> dict[str, Any]:
    """Load a manifest from JSON file."""
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_manifest(manifest: dict[str, Any], path: Path) -> None:
    """Save manifest to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
