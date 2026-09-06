import json
from pathlib import Path

import pytest

from codex_local_harness.context_qualification import (
    CompactionQualification,
    evaluate_qualification,
)


def attempt(identifier: str, **changes) -> CompactionQualification:
    values = dict(
        attempt_id=identifier,
        profile="baseline" if identifier == "baseline" else "candidate",
        primary_input_tokens=100_000,
        primary_cached_input_tokens=70_000,
        compaction_pass_input_tokens=0,
        compaction_pass_cached_input_tokens=0,
        resume_input_tokens=0,
        resume_cached_input_tokens=0,
        tool_loops=8,
        wall_seconds=600.0,
        state_retained=True,
        task_completed=True,
        task_outcome="passed",
    )
    values.update(changes)
    return CompactionQualification(**values)


def test_qualification_accounts_for_compaction_and_resume_input():
    baseline = attempt("baseline")
    candidate = attempt(
        "candidate", primary_input_tokens=65_000,
        primary_cached_input_tokens=45_000,
        compaction_pass_input_tokens=8_000, resume_input_tokens=5_000,
    )
    decision = evaluate_qualification(baseline, candidate)
    assert candidate.net_input_tokens == 78_000
    assert decision.promote is True
    assert decision.input_improvement == pytest.approx(0.22)


def test_state_or_outcome_regression_blocks_promotion_despite_token_savings():
    decision = evaluate_qualification(
        attempt("baseline"),
        attempt(
            "candidate", primary_input_tokens=40_000, primary_cached_input_tokens=30_000,
            state_retained=False, task_outcome="failed", tool_loops=9,
        ),
    )
    assert decision.promote is False
    assert decision.reasons == (
        "state-retention-failed", "task-outcome-regressed", "tool-loops-increased",
    )


def test_candidate_is_disabled_and_names_frozen_auto_only_rollback():
    root = Path(__file__).resolve().parents[1]
    baseline = json.loads(
        (root / "profiles/context-compaction-auto-only.freeze.json").read_text()
    )
    candidate = json.loads(
        (root / "profiles/context-compaction-candidate.json").read_text()
    )
    assert baseline["frozen"] is True
    assert baseline["local_opencode"] == {
        "automatic": True, "prune": True, "reserved_tokens": 16000,
        "manual": False,
    }
    assert candidate["promotion_state"] == "disabled-pending-qualification"
    assert candidate["primary_codex"]["managed_compaction"] is False
    assert candidate["rollback_profile"] == baseline["name"]
