"""Tests for role-suite v1 framework."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

# Manually add qualification to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import modules directly
from qualification.role_suite.v1 import framework, oracles, policies

# Make available in module scope
SCHEMA_VERSION = framework.SCHEMA_VERSION
ROLE_TYPES = framework.ROLE_TYPES
SUITE_CATEGORIES = framework.SUITE_CATEGORIES
REVIEWER_CATEGORIES = framework.REVIEWER_CATEGORIES
INVESTIGATOR_CATEGORIES = framework.INVESTIGATOR_CATEGORIES
TEST_STRATEGY_CATEGORIES = framework.TEST_STRATEGY_CATEGORIES
NON_CODING_CATEGORIES = framework.NON_CODING_CATEGORIES
POLICY_VARIANTS = framework.POLICY_VARIANTS
SuiteCase = framework.SuiteCase
SuiteAttempt = framework.SuiteAttempt
SuiteSummary = framework.SuiteSummary
create_reviewer_cases = framework.create_reviewer_cases
create_investigator_cases = framework.create_investigator_cases
create_test_strategy_cases = framework.create_test_strategy_cases
create_non_coding_cases = framework.create_non_coding_cases
create_end_to_end_cases = framework.create_end_to_end_cases
compute_suite_metrics = framework.compute_suite_metrics
create_manifest = framework.create_manifest
load_manifest = framework.load_manifest
save_manifest = framework.save_manifest

# Oracle functions
parse_oracle_check = oracles.parse_oracle_check
run_oracle_check = oracles.run_oracle_check
check_timeout = oracles.check_timeout
check_identity_match = oracles.check_identity_match
check_false_positive = oracles.check_false_positive
check_clean_review = oracles.check_clean_review

# Policy functions
get_policy_config = policies.get_policy_config
POLICY_DEFINITIONS = policies.POLICY_DEFINITIONS


def test_datetime_imports():
    """Import datetime for tests that use it."""
    from datetime import datetime, timezone

    return datetime, timezone


class TestSuiteCase:
    """Tests for SuiteCase dataclass."""

    def test_case_creation(self):
        """Test creating a suite case."""
        case = SuiteCase.create(
            case_id="test-case-001",
            suite="reviewer",
            category="single_defect",
            prompt="Review this patch",
            context="Patch content",
            oracle_check="defect_count:0",
            timeout_seconds=300,
            repeat=3,
        )
        assert case.case_id == "test-case-001"
        assert case.suite == "reviewer"
        assert case.category == "single_defect"
        assert case.prompt == "Review this patch"
        assert case.context == "Patch content"
        assert case.oracle_check == "defect_count:0"
        assert case.timeout_seconds == 300
        assert case.repeat == 3
        assert case.identity_hash is not None
        assert len(case.identity_hash) == 16

    def test_case_identity(self):
        """Test case identity property."""
        case = SuiteCase.create(
            case_id="test-1",
            suite="investigator",
            category="behavior_analysis",
            prompt="Analyze behavior",
            context="Context",
        )
        assert case.identity == "investigator:behavior_analysis:test-1"

    def test_case_hash_determinism(self):
        """Test that hash computation is deterministic."""
        case1 = SuiteCase.create(
            case_id="same",
            suite="reviewer",
            category="clean",
            prompt="Same prompt",
            context="Same context",
        )
        case2 = SuiteCase.create(
            case_id="same",
            suite="reviewer",
            category="clean",
            prompt="Same prompt",
            context="Same context",
        )
        assert case1.identity_hash == case2.identity_hash

    def test_case_validation(self):
        """Test case field validation."""
        # Valid case
        case = SuiteCase.create(
            case_id="valid",
            suite="reviewer",
            category="single_defect",
            prompt="Test",
            context="Context",
        )
        assert case.case_id == "valid"

        # Invalid suite
        with pytest.raises(ValueError, match="invalid suite"):
            SuiteCase.create(
                case_id="x",
                suite="invalid",
                category="single_defect",
                prompt="Test",
                context="Context",
            )

        # Empty case_id
        with pytest.raises(ValueError, match="case_id must be non-empty"):
            SuiteCase.create(
                case_id="",
                suite="reviewer",
                category="single_defect",
                prompt="Test",
                context="Context",
            )

        # Empty prompt
        with pytest.raises(ValueError, match="prompt must be non-empty"):
            SuiteCase.create(
                case_id="x",
                suite="reviewer",
                category="single_defect",
                prompt="",
                context="Context",
            )


class TestSuiteAttempt:
    """Tests for SuiteAttempt dataclass."""

    def test_attempt_creation(self):
        """Test creating a suite attempt."""
        attempt = SuiteAttempt(
            attempt_id="run-123",
            case_id="test-001",
            suite="reviewer",
            category="single_defect",
            repeat_index=1,
            model="test/model",
            route="test/route",
            policy="baseline",
            elapsed_seconds=45.5,
            state="completed",
            tokens=None,
            tool_calls=5,
            tool_errors=0,
            corrections=1,
            deterministic_correct=True,
            oracle_match=True,
            run_id="run-123",
            observed_at="2026-01-01T00:00:00Z",
        )
        assert attempt.attempt_id == "run-123"
        assert attempt.state == "completed"
        assert attempt.tool_calls == 5
        assert attempt.tool_errors == 0

    def test_attempt_state_validation(self):
        """Test attempt state validation."""
        valid_states = ["completed", "failed", "interrupted", "cancelled", "timed-out"]
        for state in valid_states:
            attempt = SuiteAttempt(
                attempt_id="x",
                case_id="x",
                suite="x",
                category="x",
                repeat_index=1,
                model="x",
                route="x",
                policy="baseline",
                elapsed_seconds=0,
                state=state,
                tokens=None,
                tool_calls=0,
                tool_errors=0,
                corrections=0,
            )
            assert attempt.state == state

        with pytest.raises(ValueError, match="invalid state"):
            SuiteAttempt(
                attempt_id="x",
                case_id="x",
                suite="x",
                category="x",
                repeat_index=1,
                model="x",
                route="x",
                policy="baseline",
                elapsed_seconds=0,
                state="invalid",
                tokens=None,
                tool_calls=0,
                tool_errors=0,
                corrections=0,
            )

    def test_attempt_corrections_validation(self):
        """Test attempt corrections validation."""
        with pytest.raises(ValueError, match="corrections must be non-negative"):
            SuiteAttempt(
                attempt_id="x",
                case_id="x",
                suite="x",
                category="x",
                repeat_index=1,
                model="x",
                route="x",
                policy="baseline",
                elapsed_seconds=0,
                state="completed",
                tokens=None,
                tool_calls=0,
                tool_errors=0,
                corrections=-1,
            )


class TestSuiteMetrics:
    """Tests for suite metrics computation."""

    def test_compute_suite_metrics(self):
        """Test computing suite metrics."""
        attempts = [
            SuiteAttempt(
                attempt_id="run-1",
                case_id="test-001",
                suite="reviewer",
                category="single_defect",
                repeat_index=1,
                model="test/model",
                route="test/route",
                policy="baseline",
                elapsed_seconds=45.0,
                state="completed",
                tokens=None,
                tool_calls=5,
                tool_errors=0,
                corrections=1,
            ),
            SuiteAttempt(
                attempt_id="run-2",
                case_id="test-001",
                suite="reviewer",
                category="single_defect",
                repeat_index=2,
                model="test/model",
                route="test/route",
                policy="baseline",
                elapsed_seconds=50.0,
                state="completed",
                tokens=None,
                tool_calls=6,
                tool_errors=1,
                corrections=1,
            ),
            SuiteAttempt(
                attempt_id="run-3",
                case_id="test-002",
                suite="reviewer",
                category="single_defect",
                repeat_index=1,
                model="test/model",
                route="test/route",
                policy="baseline",
                elapsed_seconds=40.0,
                state="failed",
                tokens=None,
                tool_calls=3,
                tool_errors=2,
                corrections=0,
            ),
        ]

        summaries = compute_suite_metrics(attempts)
        assert len(summaries) == 2

        # Find the summary for test-001
        summary_001 = next(s for s in summaries if s.case_id == "test-001")
        assert summary_001.runs == 2
        assert summary_001.completed_runs == 2
        assert summary_001.success_rate == 1.0
        assert summary_001.mean_elapsed == 47.5
        assert summary_001.mean_tool_errors == 0.5

        # Find the summary for test-002
        summary_002 = next(s for s in summaries if s.case_id == "test-002")
        assert summary_002.runs == 1
        assert summary_002.completed_runs == 0
        assert summary_002.success_rate == 0.0
        # mean_elapsed is None for failed runs since no completed elapsed values
        assert summary_002.mean_elapsed is None


class TestManifest:
    """Tests for manifest creation and persistence."""

    def test_create_manifest(self):
        """Test creating a manifest."""
        manifest = create_manifest(suites=["reviewer"])
        assert manifest["schema_version"] == SCHEMA_VERSION
        assert manifest["manifest_id"] == "role-suite-v1"
        assert "reviewer" in manifest["suites"]
        assert manifest["cases_count"] == 20  # All reviewer cases

    def test_create_manifest_multiple_suites(self):
        """Test creating manifest with multiple suites."""
        manifest = create_manifest(suites=["reviewer", "investigator"])
        assert "reviewer" in manifest["suites"]
        assert "investigator" in manifest["suites"]
        assert manifest["cases_count"] == 27  # 20 + 7

    def test_create_manifest_all_suites(self):
        """Test creating manifest with all suites."""
        # Import to ensure TokenUsage is available
        try:
            from codex_local_harness.health import TokenUsage
        except ImportError:
            pass
        manifest = create_manifest()
        assert len(manifest["suites"]) == 5
        assert manifest["cases_count"] == 60  # 20 + 7 + 5 + 24 + 4

    def test_save_and_load_manifest(self, tmp_path):
        """Test saving and loading manifest."""
        manifest = create_manifest(suites=["reviewer"])
        manifest_path = tmp_path / "manifest.json"

        save_manifest(manifest, manifest_path)
        loaded = load_manifest(manifest_path)

        assert loaded["manifest_id"] == manifest["manifest_id"]
        assert loaded["cases_count"] == manifest["cases_count"]


class TestReviewCases:
    """Tests for reviewer case generation."""

    def test_reviewer_case_count(self):
        """Test that we get 20 reviewer cases."""
        cases = create_reviewer_cases()
        assert len(cases) == 20

    def test_reviewer_case_categories(self):
        """Test reviewer case category distribution."""
        cases = create_reviewer_cases()

        single_defect = [c for c in cases if c.category == "single_defect"]
        interacting = [c for c in cases if c.category == "interacting"]
        clean = [c for c in cases if c.category == "clean"]
        inadequate_tests = [c for c in cases if c.category == "inadequate_tests"]
        concurrency_state = [c for c in cases if c.category == "concurrency_state"]

        assert len(single_defect) == 8
        assert len(interacting) == 4
        assert len(clean) == 4
        assert len(inadequate_tests) == 2
        assert len(concurrency_state) == 2

    def test_reviewer_case_oracle_checks(self):
        """Test reviewer case oracle checks."""
        cases = create_reviewer_cases()
        case_by_id = {c.case_id: c for c in cases}

        # Check a single defect case
        case = case_by_id["reviewer-sd-01"]
        assert case.oracle_check == "defect_count:1"
        assert case.repeat == 3
        assert case.timeout_seconds == 300

        # Check a clean case
        case = case_by_id["reviewer-clean-01"]
        assert case.oracle_check == "defect_count:0"
        assert case.repeat == 3

        # Check an inadequate tests case
        case = case_by_id["reviewer-ittest-01"]
        assert "test" in case.oracle_check.lower()


class TestInvestigatorCases:
    """Tests for investigator case generation."""

    def test_investigator_case_count(self):
        """Test investigator has 7 categories."""
        cases = create_investigator_cases()
        assert len(cases) == 7

    def test_investigator_categories(self):
        """Test investigator covers all 7 categories."""
        cases = create_investigator_cases()
        categories = {c.category for c in cases}
        assert categories == set(INVESTIGATOR_CATEGORIES.keys())

    def test_investigator_prompt_content(self):
        """Test investigator prompts contain analysis context."""
        cases = create_investigator_cases()
        for case in cases:
            assert "Analyze" in case.prompt or "Identify" in case.prompt
            assert case.timeout_seconds == 45
            assert case.repeat == 2


class TestTestStrategyCases:
    """Tests for test-strategy case generation."""

    def test_test_strategy_case_count(self):
        """Test test-strategy has 5 categories."""
        cases = create_test_strategy_cases()
        assert len(cases) == 5

    def test_test_strategy_categories(self):
        """Test test-strategy covers all 5 categories."""
        cases = create_test_strategy_cases()
        categories = {c.category for c in cases}
        assert categories == set(TEST_STRATEGY_CATEGORIES.keys())

    def test_test_strategy_prompt_content(self):
        """Test test-strategy prompts contain design context."""
        cases = create_test_strategy_cases()
        for case in cases:
            # Check for strategy-related keywords
            assert "strategy" in case.prompt.lower() or "test" in case.prompt.lower()


class TestNonCodingCases:
    """Tests for non-coding case generation."""

    def test_non_coding_case_count(self):
        """Test non-coding has 24 cases (6 categories × 4)."""
        cases = create_non_coding_cases()
        assert len(cases) == 24

    def test_non_coding_category_distribution(self):
        """Test 4 cases per non-coding category."""
        cases = create_non_coding_cases()

        for cat in NON_CODING_CATEGORIES.keys():
            cat_cases = [c for c in cases if c.category == cat]
            assert len(cat_cases) == 4, f"Expected 4 cases for {cat}, got {len(cat_cases)}"

    def test_non_coding_case_ids(self):
        """Test non-coding case IDs have correct format."""
        cases = create_non_coding_cases()
        for case in cases:
            # Pattern: noncoding-<category>-<index>
            assert case.case_id.startswith("noncoding-")
            assert "-0" in case.case_id or "-1" in case.case_id


class TestEndToEndCases:
    """Tests for end-to-end case generation."""

    def test_end_to_end_case_count(self):
        """Test at least 4 end-to-end cases."""
        cases = create_end_to_end_cases()
        assert len(cases) >= 4

    def test_end_to_end_categories(self):
        """Test end-to-end cases are in workflow category."""
        cases = create_end_to_end_cases()
        for case in cases:
            assert case.category == "workflow"
            assert "e2e" in case.case_id


class TestPolicyDefinitions:
    """Tests for policy configuration."""

    def test_baseline_policy(self):
        """Test baseline policy configuration."""
        policy = POLICY_DEFINITIONS["baseline"]
        assert policy.stop_on_first_failure is False
        assert policy.phased_execution is False
        assert policy.read_only is False

    def test_explicit_stop_policy(self):
        """Test explicit-stop policy configuration."""
        policy = POLICY_DEFINITIONS["explicit-stop"]
        assert policy.stop_on_first_failure is True
        assert policy.phased_execution is False

    def test_structured_phased_policy(self):
        """Test structured/phased policy configuration."""
        policy = POLICY_DEFINITIONS["structured/phased"]
        assert policy.phased_execution is True
        assert policy.checkpoint_interval == 3

    def test_invalid_policy(self):
        """Test invalid policy ID raises error."""
        with pytest.raises(ValueError, match="Unknown policy"):
            get_policy_config("invalid")


class TestOraclesIntegration:
    """Integration tests for oracle checks."""

    def test_defect_count_oracle(self):
        """Test defect count oracle check."""
        from qualification.role_suite.v1.oracles import (
            parse_oracle_check,
            run_oracle_check,
        )

        oracle = parse_oracle_check(
            "defect_count:0",
            "reviewer",
            "clean",
            "clean-001",
        )
        assert oracle.check_type == "defect_count"
        assert oracle.expected_value == 0

        # Clean output (no findings)
        output = {"schema_version": 1, "findings": []}
        result = run_oracle_check(oracle, output=output)
        assert result.passed is True

        # Output with findings
        output = {"schema_version": 1, "findings": [{"claim": "bug"}]}
        result = run_oracle_check(oracle, output=output)
        assert result.passed is False

    def test_analysis_complete_oracle(self):
        """Test analysis complete oracle check."""
        from qualification.role_suite.v1.oracles import (
            parse_oracle_check,
            run_oracle_check,
        )

        oracle = parse_oracle_check(
            "analysis_complete:true",
            "investigator",
            "behavior_analysis",
            "beh-001",
        )

        output = {"schema_version": 1, "analysis": "complete", "findings": []}
        result = run_oracle_check(oracle, output=output)
        assert result.passed is True


class TestValidationIsolation:
    """Tests for validation and isolation checks."""

    def test_validation_isolation_separation(self):
        """Test that validation and isolation are properly separated."""
        # Clean review false positive
        findings = [{"claim": "defect", "evidence": "unknown"}]
        result = check_clean_review(findings, should_have_findings=False)
        assert result.passed is False
        assert "incorrectly flagged" in result.message.lower()

        # False positive detection
        findings = [{"claim": "This is correct", "evidence": "known truth"}]
        known_truth = ["This is correct"]
        result = check_false_positive(findings, known_truth)
        assert result.passed is False


class TestTimeoutButCorrect:
    """Tests for timeout but correct scenarios."""

    def test_timeout_handling(self):
        """Test that timeout is tracked separately from correctness."""
        from qualification.role_suite.v1.oracles import check_timeout

        # Within timeout
        result = check_timeout(elapsed_seconds=45.0, timeout_seconds=60.0)
        assert result.passed is True

        # Exceeds timeout
        result = check_timeout(elapsed_seconds=90.0, timeout_seconds=60.0)
        assert result.passed is False
        assert result.actual_value == 90.0


class TestIdentityMismatch:
    """Tests for identity mismatch detection."""

    def test_identity_mismatch_detection(self):
        """Test that identity mismatches are detected."""
        from qualification.role_suite.v1.oracles import check_identity_match

        # Match
        result = check_identity_match("abc123", "abc123")
        assert result.passed is True

        # Mismatch
        result = check_identity_match("abc123", "xyz789")
        assert result.passed is False
        assert "mismatch" in result.message.lower()


class TestMetricsAggregation:
    """Tests for metrics aggregation."""

    def test_metrics_aggregation(self):
        """Test that metrics are properly aggregated."""
        from qualification.role_suite.v1.framework import (
            SuiteAttempt,
            compute_suite_metrics,
        )

        # Create attempts with varying results
        attempts = [
            SuiteAttempt(
                attempt_id="run-1",
                case_id="test-1",
                suite="reviewer",
                category="single_defect",
                repeat_index=1,
                model="test/model",
                route="test/route",
                policy="baseline",
                elapsed_seconds=30.0,
                state="completed",
                tokens=None,
                tool_calls=10,
                tool_errors=1,
                corrections=2,
            ),
            SuiteAttempt(
                attempt_id="run-2",
                case_id="test-1",
                suite="reviewer",
                category="single_defect",
                repeat_index=2,
                model="test/model",
                route="test/route",
                policy="baseline",
                elapsed_seconds=35.0,
                state="completed",
                tokens=None,
                tool_calls=12,
                tool_errors=2,
                corrections=1,
            ),
            SuiteAttempt(
                attempt_id="run-3",
                case_id="test-2",
                suite="reviewer",
                category="single_defect",
                repeat_index=1,
                model="test/model",
                route="test/route",
                policy="baseline",
                elapsed_seconds=25.0,
                state="timed-out",
                tokens=None,
                tool_calls=5,
                tool_errors=3,
                corrections=0,
            ),
        ]

        summaries = compute_suite_metrics(attempts)
        assert len(summaries) == 2

        # Check test-1 summary
        summary_1 = next(s for s in summaries if s.case_id == "test-1")
        assert summary_1.runs == 2
        assert summary_1.mean_corrections == 1.5
        assert summary_1.mean_tool_errors == 1.5

        # Check test-2 summary
        summary_2 = next(s for s in summaries if s.case_id == "test-2")
        assert summary_2.runs == 1
        assert summary_2.completed_runs == 0
        assert summary_2.success_rate == 0.0
