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
