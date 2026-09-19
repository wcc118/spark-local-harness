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
# Exact provenance: these 15 cases were factually wrong in v2 structured run
DEEPSEEK_MISSES_STRUCTURED_V2 = frozenset({
    # All 15 DeepSeek misses from v2 structured run
    "architecture-02",  # CACHE_WRITE_RACE
    "operations-02",  # DISK_EXHAUSTION
    "reconciliation-02",  # INVENTORY_MATCH
    "research-04",  # EVIDENCE_INSUFFICIENT
    "review-clean-01",  # NULL_GUARD (ambiguous clean control)
    "review-clean-04",  # ERROR_SWALLOW (ambiguous clean control)
    "specification-01",  # TIMEZONE_CRITERION, ROW_LIMIT_CRITERION
    "specification-04",  # DEDUP_CRITERION, RETRY_CRITERION
    "test-strategy-01",  # EMPTY_INPUT
    "test-strategy-03",  # ZERO_LIMIT, NEGATIVE_OFFSET
    "test-strategy-04",  # MIXED_CASE_IDENTITY
    "workflow-01",  # DIAGNOSE, PATCH, TEST, REVIEW
    "workflow-02",  # PLAN, IMPLEMENT, TEST, REVIEW
    "workflow-03",  # INVESTIGATE, PROPOSE, VERIFY, REVIEW
    "workflow-04",  # VERIFY, APPROVE
})


def _build_challenge_set() -> list[ChallengeCase]:
    """Build the deterministic 28-case challenge subset.

    The challenge set contains:
    - All 15 DeepSeek misses from v2 structured (including 2 ambiguous clean controls)
    - 2 additional clean controls (review-clean-02, review-clean-03)
    - Representative passing anchors across every suite

    Ambiguous clean controls (review-clean-01, review-clean-04) are marked as both
    DeepSeek misses AND ambiguous clean, and are excluded from promotion scoring.
    """
    from ..v2.cases import all_cases as v2_all_cases

    v2_cases = {c.case_id: c for c in v2_all_cases()}
    challenge_cases: list[ChallengeCase] = []

    # Phase 1: Add all DeepSeek misses from structured v2
    # These 15 include 2 ambiguous clean controls (review-clean-01, review-clean-04)
    for case_id in sorted(DEEPSEEK_MISSES_STRUCTURED_V2):
        if case_id in v2_cases:
            base = v2_cases[case_id]
            # Mark ambiguous controls that are also DeepSeek misses
            is_ambiguous = case_id in AMBIGUOUS_CLEAN_CONTROLS
            challenge_cases.append(ChallengeCase(
                base_case=base,
                is_challenge=True,
                is_ambiguous_clean=is_ambiguous,
                is_passing_anchor=False,
                is_deepseek_miss=True,
            ))

    # Phase 2: Add remaining clean controls that are NOT DeepSeek misses
    # (review-clean-02 and review-clean-03 - these are unambiguous clean controls)
    CLEAN_CONTROLS_TO_ADD = [cid for cid in [f"review-clean-{i:02d}" for i in range(1, 5)]
                             if cid not in AMBIGUOUS_CLEAN_CONTROLS]
    for case_id in sorted(CLEAN_CONTROLS_TO_ADD):
        if case_id in v2_cases:
            base = v2_cases[case_id]
            challenge_cases.append(ChallengeCase(
                base_case=base,
                is_challenge=True,
                is_ambiguous_clean=False,  # Not ambiguous, just a clean control
                is_passing_anchor=False,
                is_deepseek_miss=False,
            ))

    # Phase 3: Add representative passing anchors
    # These are cases DeepSeek got right in structured v2 that demonstrate stable performance
    # Select anchors from each suite to cover all case types without exceeding 30 cases
    # Exclude cases that are already DeepSeek misses or clean controls
    PASSING_ANCHORS = [
        "review-single-01",  # NULL_GUARD (reviewer suite - basic case)
        "review-single-03",  # PATH_TRAVERSAL (reviewer suite - security case)
        "review-interacting-01",  # Interacting defects (reviewer suite - first pair)
        "investigate-01",  # BOUNDARY_FILTER (investigator suite)
        "investigate-04",  # NORMALIZATION_REMOVED (investigator suite)
        "test-strategy-02",  # TIMEOUT_FALLBACK (test-strategy suite)
        "long-context-01",  # RETENTION_30_DAYS (non-coding suite)
        "architecture-01",  # RETRY_POISON_LOOP (non-coding suite)
        "research-03",  # CHOOSE_A (non-coding suite - license selection)
        "operations-01",  # DEPLOY_REGRESSION (non-coding suite)
        "specification-02",  # RECOVERY_CRITERION (non-coding suite - spec case)
    ]

    for case_id in PASSING_ANCHORS:
        if case_id in v2_cases:
            # Skip cases that are already in the challenge set
            if case_id in DEEPSEEK_MISSES_STRUCTURED_V2:
                continue
            if case_id in {"review-clean-01", "review-clean-02", "review-clean-03", "review-clean-04"}:
                continue
            base = v2_cases[case_id]
            challenge_cases.append(ChallengeCase(
                base_case=base,
                is_challenge=True,
                is_ambiguous_clean=False,
                is_passing_anchor=True,
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
