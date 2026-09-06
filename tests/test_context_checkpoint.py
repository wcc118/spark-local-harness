import json

import pytest

from codex_local_harness.context_checkpoint import (
    ContextCheckpointEvent,
    append_checkpoint,
    checkpoint_diagnostics,
    checkpoint_packet,
    latest_checkpoint,
    read_checkpoints,
)


SHA = "a" * 40


def checkpoint(**changes):
    values = dict(
        checkpoint_id="checkpoint-1",
        observed_at="2026-09-06T12:00:00Z",
        repository="owner/repo",
        issue=169,
        operation_id="issue-169",
        session_id="thread-169",
        turn_id="turn-12",
        plan_event_id="plan-4",
        plan_revision=4,
        objective="Measure and control milestone compaction",
        acceptance_criteria_refs=("issue:169#ac",),
        decision_refs=("issue:169#decision-1",),
        test_evidence_refs=("test:focused-pass",),
        relevant_files=("src/codex_local_harness/context_controller.py",),
        next_action="Run the bounded candidate",
        head_sha=SHA,
        pull_request=170,
        blocker_refs=(),
        evidence_refs=("plan:plan-4",),
    )
    values.update(changes)
    return ContextCheckpointEvent(**values)


def test_checkpoint_round_trips_append_only_without_private_content(tmp_path):
    path = tmp_path / "checkpoints.jsonl"
    item = checkpoint()
    assert append_checkpoint(path, item) is True
    assert append_checkpoint(path, item) is False
    assert read_checkpoints(path) == (item,)
    assert latest_checkpoint(read_checkpoints(path), session_id="thread-169") == item
    serialized = path.read_text()
    assert "reasoning" not in serialized
    assert "credential" not in serialized


def test_checkpoint_requires_complete_identity_and_durable_references():
    with pytest.raises(ValueError, match="OWNER/REPO"):
        checkpoint(repository="missing-owner")
    with pytest.raises(ValueError, match="exact lowercase"):
        checkpoint(head_sha="main")
    with pytest.raises(ValueError, match="acceptance_criteria_refs"):
        checkpoint(acceptance_criteria_refs=())
    with pytest.raises(ValueError, match="relative path"):
        checkpoint(relevant_files=("../secret",))


def test_checkpoint_freshness_is_causal_not_wall_clock():
    item = checkpoint()
    current = checkpoint_diagnostics(
        item, repository="owner/repo", issue=169, pull_request=170,
        head_sha=SHA, plan_event_id="plan-4", plan_revision=4,
        latest_completed_turn_id="turn-12",
    )
    assert current == ()
    stale = checkpoint_diagnostics(
        item, repository="owner/repo", issue=169, pull_request=170,
        head_sha="b" * 40, plan_event_id="plan-5", plan_revision=5,
        latest_completed_turn_id="turn-13",
    )
    assert stale == ("stale-head-sha", "stale-plan-event", "stale-plan-revision", "stale-latest-turn")


def test_checkpoint_packet_is_canonical_bounded_and_digestible():
    first, first_digest = checkpoint_packet(checkpoint())
    second, second_digest = checkpoint_packet(
        ContextCheckpointEvent.from_dict(json.loads(first))
    )
    assert first == second
    assert first_digest == second_digest
    assert len(first_digest) == 64
    with pytest.raises(ValueError, match="byte bound"):
        checkpoint_packet(checkpoint(), max_bytes=10)
