from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from codex_local_harness.dashboard import DashboardSource
from codex_local_harness.live_execution import (
    MAX_EVENTS_PER_RUN,
    MAX_RUNS,
    live_fingerprint,
    project_live_execution,
)


NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)


def _write_log(directory: Path, run_id: str, events: list[dict], *, partial: bool = False) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{run_id}.jsonl"
    content = "\n".join(json.dumps(event) for event in events)
    if partial:
        content += '\n{"type":"tool_use"'
    path.write_text(content + "\n", encoding="utf-8")
    timestamp = NOW.timestamp()
    path.touch()
    # The projector uses event time for display and file time for liveness.
    import os

    os.utime(path, (timestamp, timestamp))
    return path


def _event(kind: str, *, tool: str | None = None, status: str | None = None, command: str = "") -> dict:
    part: dict = {}
    if kind == "tool_use":
        part = {"tool": tool, "state": {"status": status, "input": {"command": command}, "output": "TOP SECRET"}}
    return {"type": kind, "timestamp": NOW_MS, "part": part, "prompt": "PRIVATE PROMPT"}


def _result(directory: Path, run_id: str, *, exit_status: int, interrupted: bool = False) -> None:
    (directory / f"{run_id}.result.json").write_text(json.dumps({
        "run_id": run_id,
        "role": "coder",
        "model": "vllm/Qwen/Qwen3.8-27B-FP8",
        "repository": "/work/example",
        "exit_status": exit_status,
        "interrupted": interrupted,
    }), encoding="utf-8")


def test_missing_and_unconfigured_sources_are_safe(tmp_path: Path) -> None:
    assert project_live_execution(None, now=NOW)["source_state"] == "unavailable"
    assert project_live_execution(tmp_path / "missing", now=NOW)["source_state"] == "missing"


def test_invalid_event_timestamp_degrades_to_explicit_unknown_time(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    event = _event("step_start")
    event["timestamp"] = 10**100
    _write_log(runs, "invalid-time", [event])

    snapshot = project_live_execution(runs, now=NOW)

    assert snapshot["events"][0]["observed_at"] is None
    assert snapshot["events"][0]["reason"] == "normalized retained v5 execution event"


def test_projects_live_worker_tool_test_and_privacy(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _write_log(runs, "active", [
        _event("step_start"),
        _event("tool_use", tool="bash", status="running", command="pytest -q --token hunter2"),
    ])

    snapshot = project_live_execution(runs, now=NOW)

    worker = next(node for node in snapshot["nodes"] if node["kind"] == "worker")
    assert worker["state"] == "running-tests"
    assert worker["issue"] is None
    assert worker["session_id"] is None
    assert any(node["kind"] == "test" for node in snapshot["nodes"])
    assert any(edge["kind"] == "uses" for edge in snapshot["edges"])
    serialized = json.dumps(snapshot)
    assert "hunter2" not in serialized
    assert "TOP SECRET" not in serialized
    assert "PRIVATE PROMPT" not in serialized


def test_projects_waiting_states_and_terminal_results(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _write_log(runs, "thinking", [_event("step_start")])
    _write_log(runs, "tool", [_event("tool_use", tool="read", status="pending")])
    _write_log(runs, "done", [_event("step_finish")])
    _write_log(runs, "failed", [_event("step_finish")])
    _write_log(runs, "stopped", [_event("step_finish")])
    _result(runs, "done", exit_status=0)
    _result(runs, "failed", exit_status=1)
    _result(runs, "stopped", exit_status=130, interrupted=True)

    snapshot = project_live_execution(runs, now=NOW)
    states = {node["run_id"]: node["state"] for node in snapshot["nodes"] if node["kind"] == "worker"}
    assert states == {
        "thinking": "waiting-inference",
        "tool": "waiting-tool",
        "done": "completed",
        "failed": "failed",
        "stopped": "interrupted",
    }


def test_canonical_evidence_closes_helper_run_without_result_file(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _write_log(runs, "helper-run", [_event("step_start"), _event("step_finish")])

    snapshot = project_live_execution(runs, now=NOW, run_evidence={
        "helper-run": {
            "terminal_state": "completed",
            "source_ref": "lifecycle:helper-run:completed",
            "role": "helper",
            "model": "lmstudio/qwen/qwen3.8-27b",
            "repository": "wcc118/codex-local-harness",
            "issue": 117,
            "elapsed_seconds": 58.9,
            "tool_calls": 2,
            "token_usage": 22868,
        },
    })

    worker = next(node for node in snapshot["nodes"] if node["kind"] == "worker")
    assert worker["state"] == "completed"
    assert worker["role"] == "helper"
    assert worker["model"] == "lmstudio/qwen/qwen3.8-27b"
    assert worker["repository"] == "wcc118/codex-local-harness"
    assert worker["issue"] == 117
    assert worker["tool_calls"] == 2
    assert worker["token_usage"] == 22868
    assert worker["source_ref"] == "lifecycle:helper-run:completed"
    assert snapshot["handoffs"][0]["from"] == "helper"


def test_stale_partial_malformed_and_bounds(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    path = _write_log(runs, "partial", [_event("step_start")], partial=True)
    import os

    old = NOW.timestamp() - 301
    os.utime(path, (old, old))
    malformed = _write_log(runs, "malformed", [_event("step_start")])
    malformed.write_text('{"type": bad}\n' + malformed.read_text(encoding="utf-8"), encoding="utf-8")
    degraded = project_live_execution(runs, now=NOW)

    assert degraded["source_state"] == "malformed"
    partial_worker = next(
        node for node in degraded["nodes"] if node.get("run_id") == "partial"
    )
    assert partial_worker["state"] == "stale"

    bounded_runs = tmp_path / "bounded"
    for index in range(MAX_RUNS + 2):
        _write_log(
            bounded_runs,
            f"run-{index:03}",
            [_event("step_start")] * (MAX_EVENTS_PER_RUN + 2),
        )

    snapshot = project_live_execution(bounded_runs, now=NOW)

    assert snapshot["truncated"]["runs"] >= 2
    assert len({node["run_id"] for node in snapshot["nodes"] if node["kind"] == "worker"}) <= MAX_RUNS


def test_dashboard_snapshot_and_fingerprint_include_live_runs(tmp_path: Path) -> None:
    runs = tmp_path / "runs.jsonl"
    adjudications = tmp_path / "adjudications.jsonl"
    lifecycle = tmp_path / "lifecycle.jsonl"
    for path in (runs, adjudications, lifecycle):
        path.write_text("", encoding="utf-8")
    v5_runs = tmp_path / "v5"
    log = _write_log(v5_runs, "active", [_event("step_start")])
    source = DashboardSource(runs, adjudications, lifecycle, now=NOW, v5_runs=v5_runs)

    before = source.fingerprint()
    assert source.snapshot()["live_execution"]["nodes"]
    log.write_text(log.read_text(encoding="utf-8") + json.dumps(_event("step_finish")) + "\n", encoding="utf-8")
    after = source.fingerprint()

    assert before != after
    assert live_fingerprint(v5_runs)


def test_microscope_projects_safe_result_handoff_and_neutral_signals(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    first = _event("tool_use", tool="read", status="completed")
    second = _event("tool_use", tool="read", status="error")
    second["timestamp"] = NOW_MS + 121_000
    _write_log(runs, "evidence", [first, second])
    (runs / "evidence.result.json").write_text(json.dumps({
        "run_id": "evidence",
        "role": "reviewer",
        "model": "safe-model",
        "repository": "/work/example",
        "session_id": "session-a",
        "issue": 106,
        "exit_status": 1,
        "elapsed_seconds": 42.5,
        "tool_calls": 2,
        "tests": [{"status": "failed", "command": "PRIVATE COMMAND"}],
        "changed_paths": ["src/safe.py"],
        "branch": "issue-106",
        "head_sha": "a" * 40,
    }), encoding="utf-8")

    snapshot = project_live_execution(runs, now=NOW)
    worker = next(node for node in snapshot["nodes"] if node["kind"] == "worker")

    assert snapshot["schema_version"] == 2
    assert worker["elapsed_seconds"] == 42.5
    assert worker["files_observed"] == ["src/safe.py"]
    assert worker["test_observations"] == 1
    assert {item["kind"] for item in snapshot["signals"]} == {
        "repeated-tool", "silent-interval", "tool-error",
    }
    assert snapshot["handoffs"][0]["from"] == "reviewer"
    assert snapshot["criteria_states"][0]["state"] == "unknown"
    assert {event["kind"] for event in snapshot["events"]} == {"tool-use", "run-result"}
    assert [event["observed_at"] or "" for event in snapshot["events"]] == sorted(
        event["observed_at"] or "" for event in snapshot["events"]
    )
    serialized = json.dumps(snapshot)
    assert "PRIVATE COMMAND" not in serialized
    assert "no failure inferred" in serialized


def test_live_projection_respects_observed_session_identity(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    for run_id, session_id in (("one", "session-a"), ("two", "session-b")):
        _write_log(runs, run_id, [_event("step_start")])
        _result(runs, run_id, exit_status=0)
        result_path = runs / f"{run_id}.result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["session_id"] = session_id
        result_path.write_text(json.dumps(result), encoding="utf-8")

    snapshot = project_live_execution(runs, now=NOW, session_id="session-a")

    assert {node["run_id"] for node in snapshot["nodes"] if node["kind"] == "worker"} == {"one"}
