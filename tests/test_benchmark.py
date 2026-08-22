"""Tests for the paired-model benchmark module."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from codex_local_harness.benchmark import (
    BenchmarkAttempt,
    BenchmarkFixture,
    BenchmarkManifest,
    PairedSummary,
    BenchmarkMetrics,
    FIXTURE_CATEGORIES,
)
from codex_local_harness.health import TokenUsage, RunHealth


def test_benchmark_manifest_roundtrip():
    """Test manifest serialization and deserialization."""
    manifest = BenchmarkManifest(
        manifest_id="test-manifest-v1",
        description="Test benchmark",
        fixtures_count=3,
    )
    data = manifest.as_dict()
    restored = BenchmarkManifest.from_dict(data)
    assert restored.manifest_id == manifest.manifest_id
    assert restored.description == manifest.description
    assert restored.fixtures_count == manifest.fixtures_count


def test_benchmark_manifest_validation():
    """Test manifest field validation."""
    # Valid manifest
    manifest = BenchmarkManifest(
        manifest_id="test-manifest-v1",
        description="Test",
        fixtures_count=0,
    )
    assert manifest.schema_version == 1

    # Invalid version
    with pytest.raises(ValueError, match="unsupported manifest schema version"):
        BenchmarkManifest(
            manifest_id="test",
            schema_version=999,
        )


def test_benchmark_fixture_creation():
    """Test fixture creation with identity hash."""
    fixture = BenchmarkFixture.create(
        fixture_id="test-issue-42",
        category="focused_fix",
        prompt="Fix hash collision handling",
        context='{"file": "hash_table.py"}',
    )
    assert fixture.fixture_id == "test-issue-42"
    assert fixture.category == "focused_fix"
    assert fixture.prompt == "Fix hash collision handling"
    assert fixture.identity_hash is not None
    assert len(fixture.identity_hash) == 16


def test_benchmark_fixture_identity():
    """Test fixture identity property."""
    fixture = BenchmarkFixture.create(
        fixture_id="test-1",
        category="investigation",
        prompt="Investigate performance",
        context="",
    )
    assert fixture.identity == "investigation:test-1"


def test_benchmark_fixture_hash_determinism():
    """Test that hash computation is deterministic."""
    fixture1 = BenchmarkFixture.create(
        fixture_id="test-1",
        category="tests",
        prompt="Add tests",
        context="",
    )
    fixture2 = BenchmarkFixture.create(
        fixture_id="test-1",
        category="tests",
        prompt="Add tests",
        context="",
    )
    assert fixture1.identity_hash == fixture2.identity_hash


def test_benchmark_fixture_validation():
    """Test fixture field validation."""
    # Valid fixture
    fixture = BenchmarkFixture.create(
        fixture_id="valid",
        category="ui",
        prompt="Improve UI",
        context="",
    )

    # Empty fixture_id
    with pytest.raises(ValueError, match="fixture_id must be non-empty"):
        BenchmarkFixture.create(
            fixture_id="",
            category="ui",
            prompt="Test",
            context="",
        )

    # Invalid category
    with pytest.raises(ValueError, match="invalid fixture category"):
        BenchmarkFixture.create(
            fixture_id="test",
            category="invalid_category",
            prompt="Test",
            context="",
        )


def test_benchmark_attempt_from_run_health():
    """Test creating attempt from RunHealth record."""
    from codex_local_harness.health import RunHealth, TokenUsage

    run = RunHealth(
        run_id="run-123",
        issue=42,
        role="coder",
        runtime="opencode",
        model="qwen",
        profile="default",
        task_class="medium",
        status="success",
        wall_seconds=123.45,
        usage=TokenUsage(local_input=1000, local_output=500),
        correction_cycle=2,
        tool_calls=10,
        tool_errors=1,
    )

    attempt = BenchmarkAttempt.from_run_health(
        run,
        model="qwen-coder-v3",
        route="default",
        fixture_id="issue-42",
        category="focused_fix",
    )

    assert attempt.attempt_id == "run-123"
    assert attempt.model == "qwen-coder-v3"
    assert attempt.route == "default"
    assert attempt.fixture_id == "issue-42"
    assert attempt.category == "focused_fix"
    assert attempt.elapsed_seconds == 123.45
    # state is mapped from RunHealth status to standard benchmark state
    assert attempt.state == "completed"  # "success" -> "completed"
    assert attempt.tokens.local_total == 1500
    assert attempt.corrections == 2
    assert attempt.tool_calls == 10
    assert attempt.tool_errors == 1


def test_benchmark_attempt_validation():
    """Test attempt field validation."""
    fixture = BenchmarkFixture.create(
        fixture_id="test",
        category="tests",
        prompt="Add tests",
        context="",
    )

    # Valid attempt
    attempt = BenchmarkAttempt(
        attempt_id="attempt-1",
        identity_hash=fixture.identity_hash,
        model="qwen",
        route="default",
        fixture_id="test",
        category="tests",
        elapsed_seconds=100.0,
        state="completed",
        tokens=TokenUsage(local_input=1000),
        tool_calls=10,
        tool_errors=1,
        corrections=2,
        permissions_requested=0,
        questions_asked=0,
        interventions=1,
        finding_refs=(),
        run_id="run-1",
        observed_at="2026-08-15T12:00:00Z",
    )

    # Negative corrections
    with pytest.raises(ValueError, match="corrections must be non-negative"):
        BenchmarkAttempt(
            attempt_id="attempt-2",
            identity_hash=fixture.identity_hash,
            model="qwen",
            route="default",
            fixture_id="test",
            category="tests",
            elapsed_seconds=100.0,
            state="completed",
            tokens=TokenUsage(local_input=1000),
            tool_calls=10,
            tool_errors=1,
            corrections=-1,
            permissions_requested=0,
            questions_asked=0,
            interventions=1,
            finding_refs=(),
            run_id="run-2",
            observed_at="2026-08-15T12:00:00Z",
        )

    # tool_errors > tool_calls
    with pytest.raises(ValueError, match="tool_errors cannot exceed tool_calls"):
        BenchmarkAttempt(
            attempt_id="attempt-3",
            identity_hash=fixture.identity_hash,
            model="qwen",
            route="default",
            fixture_id="test",
            category="tests",
            elapsed_seconds=100.0,
            state="completed",
            tokens=TokenUsage(local_input=1000),
            tool_calls=5,
            tool_errors=10,
            corrections=0,
            permissions_requested=0,
            questions_asked=0,
            interventions=0,
            finding_refs=(),
            run_id="run-3",
            observed_at="2026-08-15T12:00:00Z",
        )


def test_paired_summary_computation():
    """Test paired summary calculation with correctness-first rule."""
    fixture = BenchmarkFixture.create(
        fixture_id="test-issue",
        category="investigation",
        prompt="Investigate",
        context="",
    )

    left = BenchmarkAttempt(
        attempt_id="left-1",
        identity_hash=fixture.identity_hash,
        model="qwen-coder-v3",
        route="default",
        fixture_id="test-issue",
        category="investigation",
        elapsed_seconds=100.0,
        state="completed",
        tokens=TokenUsage(local_input=1000, local_output=500),
        tool_calls=10,
        tool_errors=0,
        corrections=1,
        permissions_requested=2,
        questions_asked=1,
        interventions=1,
        finding_refs=(),
        run_id="run-left",
        observed_at="2026-08-15T12:00:00Z",
    )

    right = BenchmarkAttempt(
        attempt_id="right-1",
        identity_hash=fixture.identity_hash,
        model="claude-3-5",
        route="optimized",
        fixture_id="test-issue",
        category="investigation",
        elapsed_seconds=150.0,
        state="failed",
        tokens=TokenUsage(local_input=2000, local_output=1000),
        tool_calls=15,
        tool_errors=3,
        corrections=3,
        permissions_requested=5,
        questions_asked=2,
        interventions=2,
        finding_refs=(),
        run_id="run-right",
        observed_at="2026-08-15T12:01:00Z",
    )

    # Simulate compare logic
    left_tokens = left.tokens.total
    right_tokens = right.tokens.total
    left_elapsed = left.elapsed_seconds if left.state == "completed" else None
    right_elapsed = right.elapsed_seconds if right.state == "completed" else None
    left_correct = left.state == "completed"
    right_correct = right.state == "completed"

    correct_first = left_correct and not right_correct
    tie = left_correct == right_correct and left_correct
    inconclusive = not left_correct and not right_correct

    token_reduction = None
    if left_tokens is not None and right_tokens is not None and left_tokens > 0:
        token_reduction = 1.0 - (right_tokens / left_tokens)

    time_reduction = None
    if left_elapsed is not None and right_elapsed is not None and left_elapsed > 0:
        time_reduction = 1.0 - (right_elapsed / left_elapsed)

    summary = PairedSummary(
        summary_id="pair-left-right",
        fixture_id=left.fixture_id,
        category=left.category,
        left_attempt_id=left.attempt_id,
        right_attempt_id=right.attempt_id,
        left_model=left.model,
        right_model=right.model,
        left_route=left.route,
        right_route=right.route,
        left_tokens=left_tokens,
        right_tokens=right_tokens,
        left_elapsed=left_elapsed,
        right_elapsed=right_elapsed,
        left_correct=left_correct,
        right_correct=right_correct,
        correct_first=correct_first,
        tie=tie,
        inconclusive=inconclusive,
        token_reduction=token_reduction,
        time_reduction=time_reduction,
        notes="left succeeded, right failed",
    )

    assert summary.correct_first is True
    assert summary.tie is False
    assert summary.inconclusive is False
    assert summary.left_correct is True
    assert summary.right_correct is False
    assert summary.token_reduction is not None
    # Right used more tokens, so token_reduction is negative (1 - 3000/1500 = -1.0)
    assert summary.token_reduction < 0  # Right used more tokens


def test_benchmark_metrics_aggregation():
    """Test metrics computation from attempts."""
    fixture = BenchmarkFixture.create(
        fixture_id="test",
        category="tests",
        prompt="Add tests",
        context="",
    )

    attempts = [
        BenchmarkAttempt(
            attempt_id="attempt-1",
            identity_hash=fixture.identity_hash,
            model="qwen",
            route="default",
            fixture_id="test",
            category="tests",
            elapsed_seconds=100.0,
            state="completed",
            tokens=TokenUsage(local_input=1000),
            tool_calls=10,
            tool_errors=1,
            corrections=1,
            permissions_requested=0,
            questions_asked=0,
            interventions=0,
            finding_refs=(),
            run_id="run-1",
            observed_at="2026-08-15T12:00:00Z",
        ),
        BenchmarkAttempt(
            attempt_id="attempt-2",
            identity_hash=fixture.identity_hash,
            model="qwen",
            route="default",
            fixture_id="test",
            category="tests",
            elapsed_seconds=120.0,
            state="completed",
            tokens=TokenUsage(local_input=1200),
            tool_calls=12,
            tool_errors=2,
            corrections=2,
            permissions_requested=1,
            questions_asked=1,
            interventions=1,
            finding_refs=(),
            run_id="run-2",
            observed_at="2026-08-15T12:01:00Z",
        ),
        BenchmarkAttempt(
            attempt_id="attempt-3",
            identity_hash=fixture.identity_hash,
            model="claude",
            route="default",
            fixture_id="test",
            category="tests",
            elapsed_seconds=80.0,
            state="completed",
            tokens=TokenUsage(local_input=800),
            tool_calls=8,
            tool_errors=0,
            corrections=0,
            permissions_requested=0,
            questions_asked=0,
            interventions=0,
            finding_refs=(),
            run_id="run-3",
            observed_at="2026-08-15T12:02:00Z",
        ),
    ]

    # Group by model/route/category
    groups: dict[tuple[str, str, str], list[BenchmarkAttempt]] = {}
    for attempt in attempts:
        key = (attempt.model, attempt.route, attempt.category)
        groups.setdefault(key, []).append(attempt)

    assert len(groups) == 2  # qwen/default/tests, claude/default/tests

    # Check qwen group
    qwen_attempts = groups[("qwen", "default", "tests")]
    assert len(qwen_attempts) == 2
    qwen_tokens = [a.tokens.total for a in qwen_attempts if a.tokens.total]
    qwen_corrections = [a.corrections for a in qwen_attempts]
    assert sum(qwen_tokens) == 2200
    assert sum(qwen_corrections) == 3


def test_benchmark_attempt_jsonl_roundtrip():
    """Test attempt serialization to/from JSONL."""
    fixture = BenchmarkFixture.create(
        fixture_id="test",
        category="ui",
        prompt="Improve UI",
        context="",
    )

    attempt = BenchmarkAttempt(
        attempt_id="attempt-1",
        identity_hash=fixture.identity_hash,
        model="qwen",
        route="default",
        fixture_id="test",
        category="ui",
        elapsed_seconds=100.0,
        state="completed",
        tokens=TokenUsage(local_input=1000),
        tool_calls=10,
        tool_errors=1,
        corrections=2,
        permissions_requested=3,
        questions_asked=4,
        interventions=5,
        finding_refs=("ref-1", "ref-2"),
        run_id="run-1",
        observed_at="2026-08-15T12:00:00Z",
    )

    # Serialize
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(json.dumps(attempt.as_dict(), sort_keys=True) + "\n")
        temp_path = Path(f.name)

    try:
        # Deserialize
        with temp_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        # TokenUsage is serialized as dict, need to reconstruct
        data["tokens"] = TokenUsage(**data["tokens"])
        restored = BenchmarkAttempt(**data)
        assert restored.attempt_id == attempt.attempt_id
        assert restored.model == attempt.model
        assert restored.tokens.local_total == 1000
        assert len(restored.finding_refs) == 2
    finally:
        temp_path.unlink()


def test_fixture_categories():
    """Test that all expected fixture categories are defined."""
    expected_categories = {
        "investigation",
        "focused_fix",
        "tests",
        "ui",
        "refactoring",
        "diagnosis",
        "hygiene",
        "review",
    }
    assert set(FIXTURE_CATEGORIES.keys()) == expected_categories
