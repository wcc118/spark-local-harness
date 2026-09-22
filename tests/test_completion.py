from __future__ import annotations

import json
import subprocess
import sys

import pytest

from codex_local_harness.completion import (
    CompletionError, acknowledge, snapshot, stop_decision,
)


def append(root, name, value):
    with (root / name).open("a") as stream:
        stream.write(json.dumps(value) + "\n")


def workflow(root, state="active", session="owner", stage="review", run="run-1", at="1"):
    append(root, "workflow.jsonl", {
        "session_id": session, "run_id": run, "repository": "example/repo",
        "issue": 1, "role": "reviewer", "stage": stage, "state": state,
        "observed_at": at,
    })


def finish(root, code=0, event="completed", run="run-1"):
    append(root, "lifecycle.jsonl", {
        "run_id": run, "event": event, "exit_status": code, "observed_at": "2",
    })


def hook(root, session="owner", **kwargs):
    return stop_decision(root, {"hook_event_name": "Stop", "session_id": session, **kwargs})


def test_running_worker_prevents_stop_even_after_continuation(tmp_path):
    workflow(tmp_path)
    for active in (False, True):
        assert hook(tmp_path, stop_hook_active=active)["decision"] == "block"
    with pytest.raises(CompletionError, match="terminal"):
        acknowledge(tmp_path, "owner", "run-1", "done")


@pytest.mark.parametrize(("code", "event", "state"), [
    (0, "completed", "passed"), (65, "completed", "failed"),
    (124, "timed-out", "interrupted"), (0, "cancelled", "interrupted"),
])
def test_terminal_outcomes_require_explicit_acknowledgement(tmp_path, code, event, state):
    workflow(tmp_path, state=state)
    finish(tmp_path, code, event)
    result = snapshot(tmp_path, "owner")[0]
    assert result["state"] == ("passed" if state == "passed" else "failed")
    assert hook(tmp_path)["decision"] == "block"
    acknowledge(tmp_path, "owner", "run-1", "Inspected artifact; escalated failure or verified delivery")
    assert hook(tmp_path) == {}


def test_other_sessions_and_user_interruption_are_untouched(tmp_path):
    workflow(tmp_path)
    assert hook(tmp_path, "other") == {}
    assert stop_decision(tmp_path, {"hook_event_name": "Interrupt", "session_id": "owner"}) == {}
    with pytest.raises(CompletionError, match="belong"):
        acknowledge(tmp_path, "other", "run-1", "done")


def test_runtime_end_is_not_delivery_completion(tmp_path):
    workflow(tmp_path, stage="local-work")
    workflow(tmp_path, stage="delivery")
    finish(tmp_path)
    workflow(tmp_path, state="passed", stage="local-work")
    assert snapshot(tmp_path, "owner")[0]["state"] == "pending"
    workflow(tmp_path, state="failed", stage="delivery")
    assert snapshot(tmp_path, "owner")[0]["state"] == "failed"


def test_missing_finalization_is_never_success(tmp_path):
    workflow(tmp_path, state="passed")
    assert snapshot(tmp_path, "owner")[0]["state"] == "pending"
    with pytest.raises(CompletionError):
        acknowledge(tmp_path, "owner", "run-1", "No inference activity")


def test_later_evidence_invalidates_old_ack(tmp_path):
    workflow(tmp_path, state="passed")
    finish(tmp_path)
    acknowledge(tmp_path, "owner", "run-1", "Verified")
    workflow(tmp_path, state="failed", at="3")
    assert hook(tmp_path)["decision"] == "block"


def test_acknowledgement_never_rewrites_evidence(tmp_path):
    workflow(tmp_path, state="failed")
    finish(tmp_path, 65)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    acknowledge(tmp_path, "owner", "run-1", "Malformed review; no publication or retry")
    assert all((tmp_path / name).read_bytes() == data for name, data in before.items())


@pytest.mark.parametrize("bad", ['{"run_id":', '[]\n', 'invalid\n'])
def test_corrupt_or_partial_evidence_blocks_stop(tmp_path, bad):
    (tmp_path / "workflow.jsonl").write_text(bad)
    result = subprocess.run([
        sys.executable, "-m", "codex_local_harness.completion", "--health", str(tmp_path), "stop-hook",
    ], input=json.dumps({"hook_event_name": "Stop", "session_id": "owner"}),
        text=True, capture_output=True)
    assert result.returncode == 0
    assert json.loads(result.stdout)["decision"] == "block"


def test_cli_status_reports_real_exit_code(tmp_path):
    workflow(tmp_path, state="failed")
    finish(tmp_path, 65)
    result = subprocess.run([
        sys.executable, "-m", "codex_local_harness.completion", "--health", str(tmp_path),
        "--session", "owner", "status",
    ], text=True, capture_output=True)
    assert result.returncode == 0
    assert json.loads(result.stdout)["runs"][0]["exit_status"] == 65


def test_unowned_missing_session_and_empty_disposition_are_rejected(tmp_path):
    assert hook(tmp_path, "")["decision"] == "block"
    with pytest.raises(CompletionError):
        snapshot(tmp_path, "")
    with pytest.raises(CompletionError):
        acknowledge(tmp_path, "owner", "run-1", " ")
