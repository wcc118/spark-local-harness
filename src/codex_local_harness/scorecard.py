"""Scorecard generator for benchmark evaluation results.

Consumes attempt artifacts and generates:
- JSON report with metrics
- Markdown report with human-readable summaries
- Separate reports for coding, specialist, lifecycle, reliability, latency/tokens, and displacement
- Holistic score that penalizes incomplete cohorts
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ScorecardConfig:
    """Configuration for scorecard generation."""
    schema_version: int = SCHEMA_VERSION
    generated_at: str = ""
    models: list[str] | None = None
    policy_variants: list[str] | None = None
    output_format: str = "json+markdown"

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported schema version")
        if not self.output_format or self.output_format not in ("json", "markdown", "json+markdown"):
            raise ValueError("invalid output format")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CohortMetrics:
    """Metrics for a single cohort."""
    cohort_id: str
    model: str
    policy: str
    attempts: int
    correct: int
    completed: int
    timed_out: int
    failed: int
    elapsed_seconds: float
    tool_calls: int
    tool_errors: int
    tokens_total: int
    correct_rate: float | None
    completion_rate: float | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Scorecard:
    """Complete scorecard with all metrics."""
    config: ScorecardConfig
    coding_cohorts: dict[str, CohortMetrics]
    specialist_cohorts: dict[str, CohortMetrics]
    lifecycle_cohorts: dict[str, CohortMetrics]
    reliability_metrics: dict[str, Any]
    latency_metrics: dict[str, Any]
    displacement_estimate: dict[str, Any]
    holistic_score: float
    holistic_breakdown: dict[str, Any]
    incomplete_cohorts: list[str]
    generated_at: str
    schema_version: int = SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["generated_at"] = result.get("generated_at") or datetime.now(timezone.utc).isoformat()
        return result

    def to_json(self) -> str:
        """Render as JSON string."""
        return json.dumps(self.as_dict(), indent=2, sort_keys=True)

    def to_markdown(self) -> str:
        """Render as Markdown string."""
        lines = [
            "# Scorecard",
            "",
            f"**Generated at:** {self.generated_at}",
            f"**Schema version:** {self.schema_version}",
            "",
            "## Holistic Score",
            "",
            f"**Score:** {self.holistic_score:.3f}",
            "",
            "### Breakdown",
            "",
        ]

        for key, value in self.holistic_breakdown.items():
            lines.append(f"- **{key}:** {value}")

        lines.extend([
            "",
            "## Coding Cohorts",
            "",
        ])

        for cohort_id, metrics in self.coding_cohorts.items():
            lines.extend([
                f"### {cohort_id}",
                "",
                f"- **Model:** {metrics.model}",
                f"- **Policy:** {metrics.policy}",
                f"- **Attempts:** {metrics.attempts}",
                f"- **Correct:** {metrics.correct}",
                f"- **Completed:** {metrics.completed}",
                f"- **Timed out:** {metrics.timed_out}",
                f"- **Failed:** {metrics.failed}",
                f"- **Correct rate:** {metrics.correct_rate}",
                f"- **Completion rate:** {metrics.completion_rate}",
                f"- **Elapsed:** {metrics.elapsed_seconds:.2f}s",
                f"- **Tools:** {metrics.tool_calls}",
                f"- **Tool errors:** {metrics.tool_errors}",
                f"- **Tokens:** {metrics.tokens_total}",
                "",
            ])

        lines.extend([
            "## Specialist Cohorts",
            "",
        ])

        for cohort_id, metrics in self.specialist_cohorts.items():
            lines.extend([
                f"### {cohort_id}",
                "",
                f"- **Model:** {metrics.model}",
                f"- **Policy:** {metrics.policy}",
                f"- **Attempts:** {metrics.attempts}",
                f"- **Correct:** {metrics.correct}",
                f"- **Completed:** {metrics.completed}",
                f"- **Timed out:** {metrics.timed_out}",
                f"- **Failed:** {metrics.failed}",
                f"- **Correct rate:** {metrics.correct_rate}",
                f"- **Completion rate:** {metrics.completion_rate}",
                f"- **Elapsed:** {metrics.elapsed_seconds:.2f}s",
                f"- **Tools:** {metrics.tool_calls}",
                f"- **Tool errors:** {metrics.tool_errors}",
                f"- **Tokens:** {metrics.tokens_total}",
                "",
            ])

        lines.extend([
            "## Lifecycle Cohorts",
            "",
        ])

        for cohort_id, metrics in self.lifecycle_cohorts.items():
            lines.extend([
                f"### {cohort_id}",
                "",
                f"- **Model:** {metrics.model}",
                f"- **Policy:** {metrics.policy}",
                f"- **Attempts:** {metrics.attempts}",
                f"- **Correct:** {metrics.correct}",
                f"- **Completed:** {metrics.completed}",
                f"- **Timed out:** {metrics.timed_out}",
                f"- **Failed:** {metrics.failed}",
                f"- **Correct rate:** {metrics.correct_rate}",
                f"- **Completion rate:** {metrics.completion_rate}",
                f"- **Elapsed:** {metrics.elapsed_seconds:.2f}s",
                f"- **Tools:** {metrics.tool_calls}",
                f"- **Tool errors:** {metrics.tool_errors}",
                f"- **Tokens:** {metrics.tokens_total}",
                "",
            ])

        lines.extend([
            "## Reliability Metrics",
            "",
            "```json",
            json.dumps(self.reliability_metrics, indent=2),
            "```",
            "",
            "## Latency/Tokens Metrics",
            "",
            "```json",
            json.dumps(self.latency_metrics, indent=2),
            "```",
            "",
            "## Estimated Codex Displacement",
            "",
            "```json",
            json.dumps(self.displacement_estimate, indent=2),
            "```",
            "",
        ])

        if self.incomplete_cohorts:
            lines.extend([
                "## Incomplete Cohorts (FAIL CLOSED)",
                "",
                "The following cohorts are incomplete and excluded from scoring:",
                "",
            ])
            for cohort in self.incomplete_cohorts:
                lines.append(f"- {cohort}")
            lines.append("")

        return "\n".join(lines)


def compute_cohort_metrics(
    results: list[dict[str, Any]],
    cohort_id: str,
    model: str,
    policy: str,
) -> CohortMetrics:
    """Compute metrics for a cohort from results."""
    attempts = len(results)
    correct = sum(1 for r in results if r.get("deterministic_correct"))
    completed = sum(1 for r in results if r.get("state") == "completed")
    timed_out = sum(1 for r in results if r.get("state") == "timed-out")
    failed = sum(1 for r in results if r.get("state") == "failed")
    elapsed = sum(r.get("elapsed_seconds", 0) for r in results)
    tools = sum(r.get("tool_calls", 0) for r in results)
    tool_errors = sum(r.get("tool_errors", 0) for r in results)
    tokens = sum(r.get("tokens_total") or 0 for r in results)

    return CohortMetrics(
        cohort_id=cohort_id,
        model=model,
        policy=policy,
        attempts=attempts,
        correct=correct,
        completed=completed,
        timed_out=timed_out,
        failed=failed,
        elapsed_seconds=round(elapsed, 3),
        tool_calls=tools,
        tool_errors=tool_errors,
        tokens_total=tokens,
        correct_rate=round(correct / attempts, 3) if attempts else None,
        completion_rate=round(completed / attempts, 3) if attempts else None,
    )


def compute_reliability_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute reliability metrics from results."""
    # Consistency across repeats
    case_policy_map: dict[tuple[str, str], list[dict]] = {}
    for r in results:
        key = (r.get("case_id", "unknown"), r.get("policy_variant", "unknown"))
        case_policy_map.setdefault(key, []).append(r)

    consistent = 0
    total_pairs = 0
    for key, group in case_policy_map.items():
        if len(group) > 1:
            total_pairs += 1
            outcomes = {r.get("deterministic_correct") for r in group}
            if len(outcomes) == 1 and None not in outcomes:
                consistent += 1

    return {
        "total_case_policy_pairs": len(case_policy_map),
        "consistent_pairs": consistent,
        "inconsistent_pairs": total_pairs - consistent,
        "consistency_rate": round(consistent / total_pairs, 3) if total_pairs else None,
        "timeouts": sum(1 for r in results if r.get("state") == "timed-out"),
        "worker_failures": sum(1 for r in results if r.get("state") == "failed"),
    }


def compute_latency_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute latency and token usage metrics."""
    if not results:
        return {
            "total_elapsed_seconds": 0,
            "avg_elapsed_per_case": 0,
            "total_tokens": 0,
            "avg_tokens_per_attempt": 0,
        }

    total_elapsed = sum(r.get("elapsed_seconds", 0) for r in results)
    total_tokens = sum(r.get("tokens_total") or 0 for r in results)

    # Group by case for per-case timing
    case_map: dict[str, list[dict]] = {}
    for r in results:
        case_map.setdefault(r.get("case_id", "unknown"), []).append(r)

    case_elapsed = [sum(r.get("elapsed_seconds", 0) for r in group) for group in case_map.values()]

    return {
        "total_elapsed_seconds": round(total_elapsed, 3),
        "avg_elapsed_per_case": round(total_elapsed / len(case_map), 3) if case_map else 0,
        "min_case_elapsed": round(min(case_elapsed) if case_elapsed else 0, 3),
        "max_case_elapsed": round(max(case_elapsed) if case_elapsed else 0, 3),
        "total_tokens": total_tokens,
        "avg_tokens_per_attempt": round(total_tokens / len(results), 3) if results else 0,
        "tool_call_counts": {
            "min": min(r.get("tool_calls", 0) for r in results),
            "max": max(r.get("tool_calls", 0) for r in results),
            "avg": round(sum(r.get("tool_calls", 0) for r in results) / len(results), 3) if results else 0,
        },
    }


def compute_displacement_estimate(
    coding_results: list[dict[str, Any]],
    specialist_results: list[dict[str, Any]],
    assumptions: dict[str, Any],
) -> dict[str, Any]:
    """Estimate Codex interventions/tokens displaced by benchmark.

    Assumptions:
    - Each benchmark attempt replaces one Codex intervention
    - Tokens displaced = benchmark tokens * estimated efficiency ratio
    - Human review time displaced = benchmark elapsed * estimated review factor

    The assumptions must be explicitly stated, not fabricated.
    """
    total_coding_attempts = len(coding_results)
    total_specialist_attempts = len(specialist_results)

    total_benchmark_tokens = sum(r.get("tokens_total") or 0 for r in coding_results)
    total_benchmark_tokens += sum(r.get("tokens_total") or 0 for r in specialist_results)

    total_benchmark_elapsed = sum(r.get("elapsed_seconds", 0) for r in coding_results)
    total_benchmark_elapsed += sum(r.get("elapsed_seconds", 0) for r in specialist_results)

    # Use assumptions from caller - these MUST be explicitly stated
    review_factor = assumptions.get("review_factor", 0.3)  # 30% of elapsed is human review
    efficiency_ratio = assumptions.get("efficiency_ratio", 0.8)  # 80% token efficiency

    displaced_interventions = total_coding_attempts + total_specialist_attempts
    displaced_tokens = int(total_benchmark_tokens * efficiency_ratio)
    displaced_review_time = round(total_benchmark_elapsed * review_factor, 3)

    return {
        "assumptions": assumptions,
        "coding_attempts": total_coding_attempts,
        "specialist_attempts": total_specialist_attempts,
        "total_attempts": displaced_interventions,
        "benchmark_tokens": total_benchmark_tokens,
        "efficiency_ratio": efficiency_ratio,
        "displaced_tokens": displaced_tokens,
        "benchmark_elapsed_seconds": round(total_benchmark_elapsed, 3),
        "review_factor": review_factor,
        "displaced_review_time_seconds": displaced_review_time,
    }


def generate_scorecard(
    coding_results: list[dict[str, Any]],
    specialist_results: list[dict[str, Any]] | None = None,
    lifecycle_results: list[dict[str, Any]] | None = None,
    config: ScorecardConfig | None = None,
    assumptions: dict[str, Any] | None = None,
) -> Scorecard:
    """Generate a complete scorecard from benchmark results.

    This function:
    1. Computes metrics for each cohort
    2. Identifies incomplete cohorts (fail closed)
    3. Computes holistic score with correctness-first weighting
    4. Generates JSON and Markdown output

    Missing/incomplete cohorts cause fail-closed behavior and are visible in the report.
    """
    if config is None:
        config = ScorecardConfig()

    if assumptions is None:
        assumptions = {
            "review_factor": 0.3,
            "efficiency_ratio": 0.8,
            "note": "Assumptions: review_factor=30% of elapsed is human review; efficiency_ratio=80% token efficiency vs Codex baseline",
        }

    # Compute metrics for each cohort
    coding_cohorts: dict[str, CohortMetrics] = {}
    specialist_cohorts: dict[str, CohortMetrics] = {}
    lifecycle_cohorts: dict[str, CohortMetrics] = {}

    # Process coding results
    coding_by_policy: dict[str, list[dict]] = {}
    for r in coding_results:
        policy = r.get("policy_variant", "unknown")
        coding_by_policy.setdefault(policy, []).append(r)

    for policy, results in coding_by_policy.items():
        if not results:
            continue
        model = results[0].get("model", "unknown")
        cohort_id = f"coding-{model}-{policy}"
        coding_cohorts[cohort_id] = compute_cohort_metrics(results, cohort_id, model, policy)

    # Process specialist results
    if specialist_results:
        spec_by_policy: dict[str, list[dict]] = {}
        for r in specialist_results:
            policy = r.get("policy", "unknown")
            spec_by_policy.setdefault(policy, []).append(r)

        for policy, results in spec_by_policy.items():
            if not results:
                continue
            model = results[0].get("model", "unknown")
            cohort_id = f"specialist-{model}-{policy}"
            specialist_cohorts[cohort_id] = compute_cohort_metrics(results, cohort_id, model, policy)

    # Process lifecycle results
    if lifecycle_results:
        life_by_policy: dict[str, list[dict]] = {}
        for r in lifecycle_results:
            policy = r.get("policy", "unknown")
            life_by_policy.setdefault(policy, []).append(r)

        for policy, results in life_by_policy.items():
            if not results:
                continue
            model = results[0].get("model", "unknown")
            cohort_id = f"lifecycle-{model}-{policy}"
            lifecycle_cohorts[cohort_id] = compute_cohort_metrics(results, cohort_id, model, policy)

    # Identify incomplete cohorts (fail closed)
    incomplete_cohorts = []

    # Minimum threshold: at least one attempt per cohort
    for cohort_id, metrics in coding_cohorts.items():
        if metrics.attempts < 1:
            incomplete_cohorts.append(cohort_id)

    for cohort_id, metrics in specialist_cohorts.items():
        if metrics.attempts < 1:
            incomplete_cohorts.append(cohort_id)

    for cohort_id, metrics in lifecycle_cohorts.items():
        if metrics.attempts < 1:
            incomplete_cohorts.append(cohort_id)

    # Fail closed: if there are no complete coding cohorts, fail with 0 score
    if len(coding_cohorts) == 0:
        incomplete_cohorts.append("no-coding-cohorts")

    # Compute holistic score (correctness-first, speed penalized)
    # Scoring: 1.0 = perfect, 0.0 = failure
    # Correctness is primary; speed is secondary; incomplete cohorts fail closed

    if incomplete_cohorts or len(coding_cohorts) == 0:
        # Fail closed: incomplete cohorts get zero score
        holistic_score = 0.0
        coding_correctness = 0
        coding_completion = 0
        time_penalty = 0
        specialist_bonus = 0.0
    else:
        # Compute correctness scores
        total_correct = sum(m.correct for m in coding_cohorts.values())
        total_attempts = sum(m.attempts for m in coding_cohorts.values())
        coding_correctness = total_correct / total_attempts if total_attempts else 0

        # Completion rate bonus (鼓励完成而非超时)
        total_completed = sum(m.completed for m in coding_cohorts.values())
        coding_completion = total_completed / total_attempts if total_attempts else 0

        # Time penalty (slower than expected reduces score)
        avg_elapsed = sum(m.elapsed_seconds for m in coding_cohorts.values()) / len(coding_cohorts) if coding_cohorts else 0
        time_penalty = min(1.0, 180.0 / avg_elapsed) if avg_elapsed else 0.5  # Cap at 1.0, default 0.5

        # Specialist bonus (if available)
        specialist_bonus = 0.0
        if specialist_cohorts:
            spec_correct = sum(m.correct for m in specialist_cohorts.values())
            spec_attempts = sum(m.attempts for m in specialist_cohorts.values())
            specialist_bonus = (spec_correct / spec_attempts) if spec_attempts else 0

        # Weighted holistic score
        # Correctness: 60%, Completion: 20%, Time efficiency: 20%
        holistic_score = (
            0.60 * coding_correctness +
            0.20 * coding_completion +
            0.20 * time_penalty
        )

    holistic_breakdown = {
        "coding_correctness": round(coding_correctness, 3) if "coding_correctness" in dir() else None,
        "coding_completion_rate": round(coding_completion, 3) if "coding_completion" in dir() else None,
        "time_efficiency": round(time_penalty, 3) if "time_penalty" in dir() else None,
        "specialist_bonus": round(specialist_bonus, 3) if "specialist_bonus" in dir() else None,
        "total_cohorts": len(coding_cohorts) + len(specialist_cohorts) + len(lifecycle_cohorts),
        "incomplete_cohorts": len(incomplete_cohorts),
    }

    # Compute reliability and latency metrics
    all_results = coding_results + (specialist_results or []) + (lifecycle_results or [])
    reliability = compute_reliability_metrics(all_results)
    latency = compute_latency_metrics(all_results)
    displacement = compute_displacement_estimate(coding_results, specialist_results or [], assumptions)

    return Scorecard(
        config=config,
        coding_cohorts=coding_cohorts,
        specialist_cohorts=specialist_cohorts,
        lifecycle_cohorts=lifecycle_cohorts,
        reliability_metrics=reliability,
        latency_metrics=latency,
        displacement_estimate=displacement,
        holistic_score=round(holistic_score, 3),
        holistic_breakdown=holistic_breakdown,
        incomplete_cohorts=incomplete_cohorts,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


def scorecard_from_attempt_dir(
    attempt_dir: Path,
    config: ScorecardConfig | None = None,
    assumptions: dict[str, Any] | None = None,
) -> Scorecard:
    """Load attempts from directory and generate scorecard.

    This function:
    1. Scans attempt directory for attempt.json files
    2. Classifies them into coding/specialist/lifecycle
    3. Generates the scorecard

    Assumes attempt.json files have "category" field to classify them.
    """
    coding_results = []
    specialist_results = []
    lifecycle_results = []

    for attempt_file in attempt_dir.glob("attempts/*/*/attempt.json"):
        try:
            data = json.loads(attempt_file.read_text(encoding="utf-8"))
            category = data.get("category", "")

            if category in ("investigation", "focused_fix", "tests", "ui",
                           "refactoring", "diagnosis", "hygiene", "review",
                           "concurrency", "cleanup"):
                coding_results.append(data)
            elif category in ("reviewer", "investigator", "test-strategy",
                             "non-coding", "end-to-end"):
                specialist_results.append(data)
            else:
                lifecycle_results.append(data)
        except (json.JSONDecodeError, KeyError):
            continue

    return generate_scorecard(
        coding_results=coding_results,
        specialist_results=specialist_results,
        lifecycle_results=lifecycle_results,
        config=config,
        assumptions=assumptions,
    )
