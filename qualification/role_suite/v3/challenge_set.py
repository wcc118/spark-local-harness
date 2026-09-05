"""Challenge subset for DeepSeek prompt-ablation experiment.

Defines a deterministic 28-case challenge subset containing:
- Every DeepSeek-structured miss from v2
- All reviewer clean controls (including ambiguous ones that remain flagged)
- Representative previously passing anchors

Ambiguous clean controls are included but marked for exclusion from promotion scoring.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..v2.cases import Case


# Ambiguous clean controls that must remain flagged for exclusion from promotion scoring
AMBIGUOUS_CLEAN_CONTROLS = frozenset({
    "review-clean-01",  # NULL_GUARD: None return can be weak null-handling
    "review-clean-04",  # ERROR_SWALLOW: False return can be swallowed-error behavior
})


@dataclass(frozen=True)
class ChallengeCase:
    """Extended case with ablation-specific metadata."""
    base_case: Case
    is_challenge: bool  # True if this case is in the challenge subset
    is_ambiguous_clean: bool  # True if this is an ambiguous clean control
    is_passing_anchor: bool  # True if this is a representative passing case
    is_deepseek_miss: bool  # True if DeepSeek missed this in v2 structured run

    @property
    def case_id(self) -> str:
        return self.base_case.case_id

    @property
    def suite(self) -> str:
        return self.base_case.suite

    @property
    def category(self) -> str:
        return self.base_case.category

    @property
    def evidence(self) -> dict[str, Any]:
        return self.base_case.evidence

    @property
    def expected(self) -> tuple[str, ...]:
        return self.base_case.expected

    @property
    def forbidden(self) -> tuple[str, ...]:
        return self.base_case.forbidden

    @property
    def timeout(self) -> int:
        return self.base_case.timeout


# DeepSeek misses from v2 structured run (based on RESULTS-issue-165.md)
# DeepSeek structured: 47/62 correct, Coder-Next structured: 40/62 correct
DEEPSEEK_MISSES_STRUCTURED_V2 = frozenset({
    # DeepSeek got these wrong in structured v2 but Coder-Next got them right
    "review-single-06",  # RESOURCE_LEAK
    "review-single-07",  # CACHE_KEY
    "review-interacting-04",  # RACE_CONDITION from review-concurrency
    "review-tests-01",  # MISSING_NEGATIVE_TEST
    "investigate-02",  # DNS_LATENCY
    "investigate-04",  # NORMALIZATION_REMOVED
    "investigate-07",  # INSUFFICIENT_EVIDENCE
    "investigate-08",  # MEMORY_PRESSURE
    "test-strategy-01",  # EMPTY_INPUT
    "test-strategy-03",  # ZERO_LIMIT, NEGATIVE_OFFSET
    "test-strategy-05",  # OLD_PRODUCER_NEW_CONSUMER
    "noncoding-long-context-04",  # EU_RESIDENCY_CONFLICT
    "noncoding-architecture-03",  # MIXED_VERSION_RISK, NO_ROLLBACK
    "noncoding-specification-04",  # DEDUP_CRITERION, RETRY_CRITERION
    "noncoding-reconciliation-02",  # INVENTORY_MATCH
    "noncoding-operations-02",  # DISK_EXHAUSTION
    "workflow-02",  # PLAN, IMPLEMENT, TEST, REVIEW
    "workflow-03",  # INVESTIGATE, PROPOSE, VERIFY, REVIEW
})


def _build_challenge_set() -> list[ChallengeCase]:
    """Build the deterministic 28-case challenge subset."""
    from ..v2.cases import all_cases as v2_all_cases

    v2_cases = {c.case_id: c for c in v2_all_cases()}
    challenge_cases: list[ChallengeCase] = []

    # Phase 1: Add all DeepSeek misses from structured v2
    for case_id in sorted(DEEPSEEK_MISSES_STRUCTURED_V2):
        if case_id in v2_cases:
            base = v2_cases[case_id]
            challenge_cases.append(ChallengeCase(
                base_case=base,
                is_challenge=True,
                is_ambiguous_clean=False,
                is_passing_anchor=False,
                is_deepseek_miss=True,
            ))

    # Phase 2: Add all reviewer clean controls (including ambiguous ones)
    for case_id in sorted([f"review-clean-{i:02d}" for i in range(1, 5)]):
        if case_id in v2_cases:
            base = v2_cases[case_id]
            is_ambiguous = case_id in AMBIGUOUS_CLEAN_CONTROLS
            challenge_cases.append(ChallengeCase(
                base_case=base,
                is_challenge=True,
                is_ambiguous_clean=is_ambiguous,
                is_passing_anchor=False,
                is_deepseek_miss=False,
            ))

    # Phase 3: Add representative passing anchors
    # These are cases DeepSeek got right in structured v2 that demonstrate stable performance
    PASSING_ANCHORS = [
        "review-single-01",  # NULL_GUARD (basic case)
        "review-single-02",  # AUTH_BYPASS
        "review-interacting-01",  # Interacting defects (first pair)
        "investigate-01",  # BOUNDARY_FILTER
        "investigate-03",  # SCHEMA_MISMATCH
        "test-strategy-02",  # TIMEOUT_FALLBACK
        "noncoding-long-context-01",  # RETENTION_30_DAYS, LEGAL_HOLD_OVERRIDE
        "noncoding-architecture-01",  # RETRY_POISON_LOOP
        "noncoding-research-03",  # CHOOSE_A (license selection)
        "noncoding-operations-01",  # DEPLOY_REGRESSION
        "workflow-01",  # DIAGNOSE, PATCH, TEST, REVIEW
        "workflow-04",  # VERIFY, APPROVE (clean patch case)
    ]

    for case_id in PASSING_ANCHORS:
        if case_id in v2_cases:
            base = v2_cases[case_id]
            challenge_cases.append(ChallengeCase(
                base_case=base,
                is_challenge=True,
                is_ambiguous_clean=False,
                is_passing_anchor=True,
                is_deepseek_miss=False,
            ))

    # Phase 4: Add a few more edge cases to reach ~28 cases
    # Include some previously ambiguous cases and additional variety
    ADDITIONAL_CASES = [
        "review-concurrency-01",  # RACE_CONDITION (explicit concurrency test)
        "review-concurrency-02",  # State machine race
        "test-strategy-06",  # Behavioral assertion
    ]

    for case_id in ADDITIONAL_CASES:
        if case_id in v2_cases:
            base = v2_cases[case_id]
            challenge_cases.append(ChallengeCase(
                base_case=base,
                is_challenge=True,
                is_ambiguous_clean=False,
                is_passing_anchor=False,
                is_deepseek_miss=False,
            ))

    # Sort by case_id for deterministic ordering
    challenge_cases.sort(key=lambda c: c.case_id)

    return challenge_cases


# Public API
ALL_CHALLENGE_CASES = _build_challenge_set()


def get_challenge_cases() -> list[Case]:
    """Return the challenge cases as base Case objects (for runner compatibility)."""
    return [c.base_case for c in ALL_CHALLENGE_CASES]


def get_challenge_meta() -> dict[str, ChallengeCase]:
    """Return metadata for all challenge cases."""
    return {c.case_id: c for c in ALL_CHALLENGE_CASES}


def get_ambiguous_case_ids() -> frozenset[str]:
    """Return IDs of ambiguous clean controls that should be excluded from promotion scoring."""
    return AMBIGUOUS_CLEAN_CONTROLS


def get_deepseek_miss_case_ids() -> frozenset[str]:
    """Return IDs of cases DeepSeek missed in v2 structured run."""
    return DEEPSEEK_MISSES_STRUCTURED_V2
