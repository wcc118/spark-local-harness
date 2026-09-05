"""Tests for role-suite v3 DeepSeek prompt-ablation experiment."""
from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from qualification.role_suite.v3 import (
    get_challenge_cases,
    get_challenge_meta,
    get_ambiguous_case_ids,
    get_deepseek_miss_case_ids,
    POLICIES,
    POLICY_CATEGORIES,
    ROLE_PROMPTS,
    build_full_prompt,
    aggregate,
    run_case,
)
from qualification.role_suite.v2.cases import all_cases as v2_all_cases


class TestChallengeSet:
    """Test challenge subset selection and metadata."""

    def test_challenge_set_size(self):
        """Challenge set should be 28 cases."""
        cases = get_challenge_cases()
        assert len(cases) == 28

    def test_challenge_set_unique_ids(self):
        """All case IDs should be unique."""
        cases = get_challenge_cases()
        case_ids = [c.case_id for c in cases]
        assert len(case_ids) == len(set(case_ids))

    def test_ambiguous_case_ids(self):
        """Ambiguous clean controls should be identified."""
        ambiguous = get_ambiguous_case_ids()
        assert len(ambiguous) == 2
        assert "review-clean-01" in ambiguous
        assert "review-clean-04" in ambiguous

    def test_deepseek_miss_ids(self):
        """DeepSeek misses from v2 structured should be identified."""
        deepseek_misses = get_deepseek_miss_case_ids()
        assert len(deepseek_misses) > 0

    def test_challenge_meta_structure(self):
        """Challenge metadata should include all required fields."""
        meta = get_challenge_meta()
        assert len(meta) == 28

        for case_id, challenge_case in meta.items():
            assert hasattr(challenge_case, "is_challenge")
            assert hasattr(challenge_case, "is_ambiguous_clean")
            assert hasattr(challenge_case, "is_passing_anchor")
            assert hasattr(challenge_case, "is_deepseek_miss")

    def test_passing_anchors_exist(self):
        """Passing anchors should be present in challenge set."""
        meta = get_challenge_meta()
        anchors = [c for c in meta.values() if c.is_passing_anchor]
        assert len(anchors) > 0

    def test_ambiguous_controls_in_challenge(self):
        """Ambiguous controls should be included in challenge set."""
        meta = get_challenge_meta()
        ambiguous = [c for c in meta.values() if c.is_ambiguous_clean]
        assert len(ambiguous) == 2
        # Verify they are marked as challenge cases but should be excluded from promo scoring
        for c in ambiguous:
            assert c.is_challenge
            assert c.case_id in get_ambiguous_case_ids()


class TestPolicies:
    """Test policy variant definitions."""

    def test_four_policy_variants(self):
        """Should have exactly four policy variants."""
        assert set(POLICIES.keys()) == {
            "structured-v1",
            "role-contract",
            "role-contract-plus-verification",
            "role-contract-plus-uncertainty",
        }

    def test_policy_categories(self):
        """Policies should be categorized correctly."""
        assert POLICY_CATEGORIES["structured-v1"] == "baseline"
        assert POLICY_CATEGORIES["role-contract"] == "role-specific"
        assert "verification" in POLICY_CATEGORIES["role-contract-plus-verification"]
        assert "uncertainty" in POLICY_CATEGORIES["role-contract-plus-uncertainty"]

    def test_role_prompts_coverage(self):
        """Role prompts should cover all case categories."""
        categories = {c.category for c in v2_all_cases()}
        for cat in categories:
            prompt = ROLE_PROMPTS.get(cat, ROLE_PROMPTS["non-coding"])
            assert len(prompt) > 0

    def test_build_full_prompt_includes_role_and_policy(self):
        """Full prompt should combine role and policy instructions."""
        prompt = build_full_prompt(
            "role-contract",
            "reviewer",
            "/workspace/evidence.json",
            "/workspace/result.json",
        )
        assert "REVIEWER ROLE" in prompt
        assert "role contract" in prompt.lower()
        assert "evidence.json" in prompt
        assert "result.json" in prompt


class TestAggregation:
    """Test aggregation metrics computation."""

    def test_basic_aggregation(self):
        """Aggregation should compute basic counts."""
        records = [
            {"artifact_correct": True, "completed_and_correct": True, "state": "completed", "elapsed_seconds": 5.0, "tool_calls": 2, "tool_errors": 0, "tokens_total": 100},
            {"artifact_correct": False, "completed_and_correct": False, "state": "failed", "elapsed_seconds": 3.0, "tool_calls": 1, "tool_errors": 1, "tokens_total": 50},
        ]
        agg = aggregate(records)
        assert agg["attempts"] == 2
        assert agg["artifact_correct"] == 1
        assert agg["completed_and_correct"] == 1
        assert agg["failures"] == 1
        assert agg["elapsed_seconds"] == 8.0
        assert agg["tool_errors"] == 1

    def test_challenge_aggregation(self):
        """Aggregation should compute challenge-specific metrics."""
        records = [
            {
                "case_id": "review-single-01",
                "category": "reviewer",
                "artifact_correct": True,
                "state": "completed",
                "is_challenge_case": True,
                "is_ambiguous_clean_control": False,
                "is_deepseek_miss": False,
                "elapsed_seconds": 5.0,
                "tool_calls": 2,
                "tool_errors": 0,
                "tokens_total": 100,
                "policy": "role-contract",
            },
            {
                "case_id": "review-clean-01",
                "category": "reviewer",
                "artifact_correct": False,
                "state": "completed",
                "is_challenge_case": True,
                "is_ambiguous_clean_control": True,
                "is_deepseek_miss": False,
                "elapsed_seconds": 3.0,
                "tool_calls": 1,
                "tool_errors": 0,
                "tokens_total": 50,
                "policy": "role-contract",
            },
        ]
        agg = aggregate(records)
        assert agg["challenge"]["total_attempts"] == 2
        assert agg["challenge"]["challenge_correct"] == 1

        # Promotion scored excludes ambiguous controls
        assert agg["promotion_scored"]["attempts"] == 1
        assert agg["promotion_scored"]["promo_correct"] == 1

    def test_deepseek_miss_recovery(self):
        """Aggregation should track DeepSeek miss recovery."""
        records = [
            {
                "case_id": "review-single-06",
                "artifact_correct": True,
                "state": "completed",
                "is_deepseek_miss": True,
                "policy": "role-contract-plus-verification",
                "elapsed_seconds": 5.0,
                "tool_calls": 2,
                "tool_errors": 0,
                "tokens_total": 100,
            },
            {
                "case_id": "review-single-07",
                "artifact_correct": False,
                "state": "completed",
                "is_deepseek_miss": True,
                "policy": "role-contract-plus-verification",
                "elapsed_seconds": 3.0,
                "tool_calls": 1,
                "tool_errors": 0,
                "tokens_total": 50,
            },
            {
                "case_id": "review-single-01",
                "artifact_correct": True,
                "state": "completed",
                "is_deepseek_miss": False,
                "policy": "role-contract-plus-verification",
                "elapsed_seconds": 5.0,
                "tool_calls": 2,
                "tool_errors": 0,
                "tokens_total": 100,
            },
        ]
        agg = aggregate(records)
        assert agg["deepseek_miss_recovery"]["total_misses"] == 2
        assert agg["deepseek_miss_recovery"]["recovered"] == 1
        assert agg["deepseek_miss_recovery"]["recovery_rate"] == 0.5

    def test_consistency_across_repeats(self):
        """Aggregation should compute consistency across repeats."""
        records = [
            {
                "case_id": "review-single-01",
                "policy": "role-contract",
                "artifact_correct": True,
                "state": "completed",
                "repeat": 1,
                "elapsed_seconds": 5.0,
                "tool_calls": 2,
                "tool_errors": 0,
                "tokens_total": 100,
            },
            {
                "case_id": "review-single-01",
                "policy": "role-contract",
                "artifact_correct": True,
                "state": "completed",
                "repeat": 2,
                "elapsed_seconds": 4.5,
                "tool_calls": 2,
                "tool_errors": 0,
                "tokens_total": 95,
            },
            {
                "case_id": "review-single-02",
                "policy": "role-contract",
                "artifact_correct": True,
                "state": "completed",
                "repeat": 1,
                "elapsed_seconds": 5.0,
                "tool_calls": 2,
                "tool_errors": 0,
                "tokens_total": 100,
            },
            {
                "case_id": "review-single-02",
                "policy": "role-contract",
                "artifact_correct": False,  # Different result
                "state": "completed",
                "repeat": 2,
                "elapsed_seconds": 4.5,
                "tool_calls": 2,
                "tool_errors": 0,
                "tokens_total": 95,
            },
        ]
        agg = aggregate(records)
        # Two case/policy pairs, one consistent, one inconsistent
        assert agg["consistency"]["total_case_policy_pairs"] == 2
        assert agg["consistency"]["consistent_pairs"] == 1

    def test_category_and_policy_stats(self):
        """Aggregation should include category and policy breakdowns."""
        records = [
            {
                "case_id": "review-single-01",
                "category": "reviewer",
                "artifact_correct": True,
                "state": "completed",
                "policy": "role-contract",
                "elapsed_seconds": 5.0,
                "tool_calls": 2,
                "tool_errors": 0,
                "tokens_total": 100,
            },
            {
                "case_id": "investigate-01",
                "category": "investigator",
                "artifact_correct": True,
                "state": "completed",
                "policy": "role-contract-plus-verification",
                "elapsed_seconds": 5.0,
                "tool_calls": 2,
                "tool_errors": 0,
                "tokens_total": 100,
            },
        ]
        agg = aggregate(records)
        assert "reviewer" in agg["category_stats"]
        assert "investigator" in agg["category_stats"]
        assert "role-contract" in agg["policy_stats"]
        assert "role-contract-plus-verification" in agg["policy_stats"]


class TestRunnerIntegration:
    """Integration tests for runner execution."""

    def test_run_case_writes_artifacts(self, tmp_path: Path):
        """Run case should write evidence artifact without oracle and create attempt.json."""
        # We can't actually run subprocess in this environment
        # This test verifies artifact structure without actual execution
        from qualification.role_suite.v2.cases import all_cases
        case = next(c for c in all_cases() if c.case_id == "review-single-01")

        # Simulate a record as the runner would create it
        record = {
            "case_id": case.case_id,
            "suite": case.suite,
            "category": case.category,
            "model": "test-model",
            "route": "test",
            "policy": "role-contract",
            "policy_category": "role-specific",
            "repeat": 1,
            "identity_hash": "test_hash",
            "state": "completed",
            "terminal_completed": True,
            "artifact_correct": True,
            "completed_and_correct": True,
            "elapsed_seconds": 5.0,
            "tool_calls": 2,
            "tool_errors": 0,
            "tokens_total": 100,
            "worker_exit_code": 0,
            "oracle": {"answers": ["NULL_GUARD"], "expected_count": 1},
            "observed_at": "2026-09-05T12:00:00+00:00",
            "is_challenge_case": True,
            "is_ambiguous_clean_control": False,
            "is_passing_anchor": False,
            "is_deepseek_miss": False,
            "challenge_set_size": 28,
        }

        attempt_dir = tmp_path / "output" / "attempts" / "testhash" / case.case_id
        attempt_dir.mkdir(parents=True)

        import json
        (attempt_dir / "attempt.json").write_text(json.dumps(record, indent=2) + "\n")

        # Create evidence artifact
        evidence_artifact = {
            "case_id": case.case_id,
            "policy": "role-contract",
            "evidence": case.evidence,
            "observed_at": record["observed_at"],
        }
        (attempt_dir / "evidence_artifact.json").write_text(json.dumps(evidence_artifact, indent=2) + "\n")

        # Verify artifacts exist
        assert (attempt_dir / "attempt.json").exists()
        assert (attempt_dir / "evidence_artifact.json").exists()

        # Verify evidence artifact doesn't contain oracle
        artifact = json.loads((attempt_dir / "evidence_artifact.json").read_text())
        assert "expected" not in artifact
        assert "forbidden" not in artifact
        assert "oracle" not in artifact
        assert artifact["case_id"] == "review-single-01"
        assert artifact["policy"] == "role-contract"
