import pytest

from codex_local_harness.context_controller import (
    CompactionPolicy,
    CompactionReadiness,
    CompactionRejected,
    CompactionTracker,
    authorize_compaction,
    evaluate_compaction,
)


NOW = "2026-09-06T12:00:00Z"


def readiness(**changes):
    values = dict(
        controller_owned=True,
        thread_idle=True,
        tool_active=False,
        checkpoint_current=True,
        milestone_completed=True,
        terminal_milestone=False,
        remaining_work=True,
        last_input_tokens=161_000,
        context_window=258_400,
        turns_since_compaction=3,
    )
    values.update(changes)
    return CompactionReadiness(**values)


def test_candidate_is_default_off_and_advisory_only():
    decision = evaluate_compaction(readiness())
    assert decision.action == "advisory"
    assert decision.pressure == pytest.approx(161_000 / 258_400)
    with pytest.raises(CompactionRejected):
        authorize_compaction(readiness(), CompactionPolicy())


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"controller_owned": False}, "session-not-controller-owned"),
        ({"thread_idle": False}, "thread-active"),
        ({"tool_active": True}, "tool-active"),
        ({"checkpoint_current": False}, "checkpoint-stale"),
        ({"milestone_completed": False}, "milestone-incomplete"),
        ({"terminal_milestone": True}, "terminal-milestone"),
        ({"remaining_work": False}, "no-remaining-work"),
        ({"last_input_tokens": None}, "context-unavailable"),
        ({"last_input_tokens": 120_000}, "below-pressure-threshold"),
        ({"turns_since_compaction": 1}, "compaction-cooldown"),
    ],
)
def test_request_rejects_every_fail_closed_gate(changes, reason):
    policy = CompactionPolicy(enabled=True)
    decision = evaluate_compaction(readiness(**changes), policy)
    assert decision.action in {"reject", "advisory"}
    assert reason in decision.reasons
    with pytest.raises(CompactionRejected, match=reason):
        authorize_compaction(readiness(**changes), policy)


def test_eligible_request_requires_all_gates():
    decision = authorize_compaction(readiness(), CompactionPolicy(enabled=True))
    assert decision.action == "request"
    assert decision.reasons == ()


def test_manual_lifecycle_uses_next_ordinary_turn_for_after_size():
    tracker = CompactionTracker(
        run_id="run-169", session_id="thread-169", repository="owner/repo", issue=169,
    )
    requested = tracker.requested(
        compaction_id="compact-1", observed_at=NOW,
        before_input_tokens=161_000, context_window=258_400,
    )
    started = tracker.observe({
        "method": "item/started", "params": {
            "threadId": "thread-169", "turnId": "turn-compact",
            "item": {"type": "contextCompaction", "id": "item-compact"},
        },
    }, observed_at="2026-09-06T12:00:01Z")
    completed = tracker.observe({
        "method": "item/completed", "params": {
            "threadId": "thread-169", "turnId": "turn-compact",
            "item": {"type": "contextCompaction", "id": "item-compact"},
        },
    }, observed_at="2026-09-06T12:00:02Z")
    during = tracker.observe({
        "method": "thread/tokenUsage/updated", "params": {
            "threadId": "thread-169", "turnId": "turn-compact",
            "tokenUsage": {"last": {"inputTokens": 10}, "modelContextWindow": 258_400},
        },
    }, observed_at="2026-09-06T12:00:03Z")
    after = tracker.observe({
        "method": "thread/tokenUsage/updated", "params": {
            "threadId": "thread-169", "turnId": "turn-next",
            "tokenUsage": {"last": {"inputTokens": 22_000}, "modelContextWindow": 258_400},
        },
    }, observed_at="2026-09-06T12:01:00Z")
    assert [requested.state, started.state, completed.state] == ["requested", "started", "completed"]
    assert during is None
    assert after.state == "post-context-observed"
    assert after.before_input_tokens == 161_000
    assert after.after_input_tokens == 22_000
    assert "summary" not in repr(after)


def test_automatic_compaction_is_distinguished_and_scoped():
    tracker = CompactionTracker(
        run_id="run-169", session_id="thread-169", repository="owner/repo", issue=169,
    )
    assert tracker.observe({
        "method": "item/started", "params": {
            "threadId": "other", "turnId": "turn-x",
            "item": {"type": "contextCompaction", "id": "item-x"},
        },
    }, observed_at=NOW) is None
    event = tracker.observe({
        "method": "item/started", "params": {
            "threadId": "thread-169", "turnId": "turn-auto",
            "item": {"type": "contextCompaction", "id": "item-auto"},
        },
    }, observed_at=NOW)
    assert event.trigger == "automatic"
    assert event.session_id == "thread-169"


def test_failed_compaction_turn_is_recorded_without_error_content():
    tracker = CompactionTracker(
        run_id="run-169", session_id="thread-169", repository="owner/repo", issue=169,
    )
    tracker.requested(
        compaction_id="compact-1", observed_at=NOW,
        before_input_tokens=160_000, context_window=258_400,
    )
    tracker.observe({
        "method": "item/started", "params": {
            "threadId": "thread-169", "turnId": "turn-compact",
            "item": {"type": "contextCompaction", "id": "item-compact"},
        },
    }, observed_at=NOW)
    failed = tracker.observe({
        "method": "turn/completed", "params": {
            "threadId": "thread-169",
            "turn": {"id": "turn-compact", "status": "failed", "error": {"message": "PRIVATE"}},
        },
    }, observed_at="2026-09-06T12:00:02Z")
    assert failed.state == "failed"
    assert failed.error_class == "compaction-turn-failed"
    assert "PRIVATE" not in repr(failed)
