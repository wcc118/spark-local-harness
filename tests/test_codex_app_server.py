from __future__ import annotations

import json
from collections import deque
from pathlib import Path

import pytest

from codex_local_harness.codex_app_server import (
    AppServerError,
    CurrentCheckpointState,
    ManagedCodexController,
)
from codex_local_harness.context_checkpoint import ContextCheckpointEvent
from codex_local_harness.context_controller import (
    CompactionPolicy,
    CompactionRejected,
    read_compaction_records,
)


SHA = "a" * 40


def checkpoint(session_id: str, turn_id: str = "turn-work") -> ContextCheckpointEvent:
    return ContextCheckpointEvent(
        checkpoint_id=f"checkpoint-{session_id}",
        observed_at="2026-09-06T12:00:00Z",
        repository="owner/repo",
        issue=169,
        pull_request=170,
        operation_id="issue-169",
        session_id=session_id,
        turn_id=turn_id,
        plan_event_id="plan-169-2",
        plan_revision=2,
        objective="Reduce context carried by long orchestrator runs.",
        acceptance_criteria_refs=("issue:169#acceptance",),
        decision_refs=("issue:169#decision-1",),
        test_evidence_refs=("check:focused-tests",),
        blocker_refs=("issue:169#blockers-none",),
        relevant_files=("src/codex_local_harness/codex_app_server.py",),
        next_action="Run independent review.",
        head_sha=SHA,
    )


class FakeTransport:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict]] = []
        self.notifications: deque[dict] = deque()
        self.fail_compaction = False

    def request(self, method, params, *, timeout, on_notification=None):
        self.requests.append((method, params))
        if method == "thread/start":
            return {"thread": {"id": "thread-169"}}
        if method == "turn/start":
            return {"turn": {"id": "turn-seed"}}
        if method == "thread/compact/start":
            if self.fail_compaction:
                raise AppServerError("app-server response timed out")
            self.notifications.extend((
                {
                    "method": "item/started",
                    "params": {
                        "threadId": "thread-169", "turnId": "turn-compact",
                        "item": {"id": "item-compact", "type": "contextCompaction"},
                    },
                },
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": "thread-169", "turnId": "turn-compact",
                        "item": {"id": "item-compact", "type": "contextCompaction"},
                    },
                },
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": "thread-169",
                        "turn": {"id": "turn-compact", "status": "completed"},
                    },
                },
            ))
            return {}
        raise AssertionError(method)

    def receive(self, *, timeout):
        if not self.notifications:
            raise AppServerError("app-server response timed out")
        return self.notifications.popleft()


def controller(tmp_path: Path, transport: FakeTransport) -> ManagedCodexController:
    return ManagedCodexController(
        transport,
        run_id="run-169",
        operation_id="issue-169",
        repository="owner/repo",
        issue=169,
        records=tmp_path / "compaction.jsonl",
        policy=CompactionPolicy(enabled=True),
    )


def current(**changes) -> CurrentCheckpointState:
    values = dict(
        repository="owner/repo", issue=169, pull_request=170, head_sha=SHA,
        plan_event_id="plan-169-2", plan_revision=2,
        milestone_completed=True, terminal_milestone=False, remaining_work=True,
        turns_since_compaction=3,
    )
    values.update(changes)
    return CurrentCheckpointState(**values)


def ready_controller(tmp_path: Path) -> tuple[ManagedCodexController, FakeTransport]:
    transport = FakeTransport()
    managed = controller(tmp_path, transport)
    managed.thread_id = "thread-169"
    managed.latest_completed_turn_id = "turn-work"
    managed.last_input_tokens = 170_000
    managed.context_window = 258_400
    from codex_local_harness.context_controller import CompactionTracker
    managed.tracker = CompactionTracker(
        run_id="run-169", session_id="thread-169",
        repository="owner/repo", issue=169,
    )
    return managed, transport


def test_issue_switch_starts_fresh_thread_from_bounded_checkpoint(tmp_path: Path):
    transport = FakeTransport()
    managed = controller(tmp_path, transport)
    thread_id, turn_id = managed.start_fresh_issue_thread(
        checkpoint("previous-thread"), cwd=tmp_path,
    )
    assert (thread_id, turn_id) == ("thread-169", "turn-seed")
    assert [method for method, _ in transport.requests] == ["thread/start", "turn/start"]
    packet = transport.requests[1][1]["input"][0]["text"]
    payload = json.loads(packet)
    assert payload["issue"] == 169
    assert payload["acceptance_criteria_refs"] == ["issue:169#acceptance"]
    assert "conversation" not in packet


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda managed: setattr(managed, "turn_active", True), "thread-active"),
        (lambda managed: setattr(managed, "tool_active", True), "tool-active"),
        (lambda managed: setattr(managed, "thread_id", None), "session-not-controller-owned"),
    ],
)
def test_compaction_rejects_unowned_or_active_state(tmp_path, mutation, reason):
    managed, _ = ready_controller(tmp_path)
    mutation(managed)
    with pytest.raises(CompactionRejected, match=reason):
        managed.request_compaction(checkpoint("thread-169"), current())


def test_compaction_rejects_stale_durable_identity(tmp_path: Path):
    managed, _ = ready_controller(tmp_path)
    with pytest.raises(CompactionRejected, match="stale-head-sha"):
        managed.request_compaction(
            checkpoint("thread-169"), current(head_sha="b" * 40),
        )


def test_controller_waits_for_context_compaction_completion(tmp_path: Path):
    managed, transport = ready_controller(tmp_path)
    completed = managed.request_compaction(checkpoint("thread-169"), current())
    assert completed.state == "completed"
    assert transport.requests[-1] == (
        "thread/compact/start", {"threadId": "thread-169"},
    )
    records = read_compaction_records(tmp_path / "compaction.jsonl")
    assert [item.state for item in records] == ["requested", "started", "completed"]
    assert all(item.before_input_tokens == 170_000 for item in records)


def test_failed_app_server_request_is_recorded_without_error_text(tmp_path: Path):
    managed, transport = ready_controller(tmp_path)
    transport.fail_compaction = True
    with pytest.raises(AppServerError, match="timed out"):
        managed.request_compaction(checkpoint("thread-169"), current())
    records = read_compaction_records(tmp_path / "compaction.jsonl")
    assert [item.state for item in records] == ["requested", "failed"]
    assert records[-1].error_class == "app-server-timeout"
    assert "response timed out" not in json.dumps(records[-1].as_dict())
