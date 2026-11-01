import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from codex_local_harness.opencode_bridge import BridgeError, run_bridge


PASSWORD = "bridge-secret"


class FakeOpenCode(BaseHTTPRequestHandler):
    prompt = None

    def _authorized(self):
        expected = "Basic " + base64.b64encode(f"opencode:{PASSWORD}".encode()).decode()
        return self.headers.get("Authorization") == expected

    def _json(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
        elif self.path == "/global/health":
            self._json(200, {"healthy": True, "version": "1.18.21"})
        elif self.path == "/config":
            self._json(200, {
                "permission": {"bash": "ask"},
                "compaction": {"auto": True, "prune": True, "reserved": 16000},
                "agent": {"coder": {"permission": {"edit": "allow"}}},
                "provider": {"secret": "must-not-be-retained"},
            })
        elif self.path == "/event":
            events = [
                {"type": "message.part.updated", "properties": {
                    "sessionID": "other-session", "part": {"type": "text", "text": "PRIVATE OTHER"},
                }},
                {"type": "message.part.updated", "properties": {
                    "sessionID": "session-123", "part": {
                        "type": "tool", "tool": "bash", "state": {"status": "pending", "input": {"command": "pytest -q"}},
                    },
                }},
                {"type": "message.updated", "properties": {
                    "info": {"id": "assistant-1", "sessionID": "session-123", "role": "assistant"},
                }},
                {"type": "message.part.updated", "properties": {
                    "part": {"type": "text", "sessionID": "session-123", "messageID": "assistant-1", "text": "done"},
                }},
                {"type": "message.part.updated", "properties": {
                    "part": {"type": "reasoning", "sessionID": "session-123", "messageID": "assistant-1", "text": "thinking"},
                }},
                {"type": "permission.updated", "properties": {
                    "id": "permission-1", "sessionID": "session-123", "type": "bash",
                    "pattern": "pytest -q", "title": "Run tests", "metadata": {},
                }},
                {"type": "question.asked", "properties": {
                    "id": "que-1", "sessionID": "session-123", "questions": [],
                }},
                {"id": "compact-start", "type": "session.next.compaction.started", "properties": {
                    "timestamp": 1, "sessionID": "session-123", "reason": "auto",
                }},
                {"id": "compact-delta", "type": "session.next.compaction.delta", "properties": {
                    "timestamp": 2, "sessionID": "session-123", "text": "PRIVATE COMPACTION SUMMARY",
                }},
                {"id": "compact-end", "type": "session.next.compaction.ended", "properties": {
                    "timestamp": 3, "sessionID": "session-123", "text": "PRIVATE COMPACTION SUMMARY",
                }},
                {"type": "session.status", "properties": {
                    "sessionID": "session-123", "status": {"type": "idle"},
                }},
            ]
            body = "".join(f"data: {json.dumps(event)}\n\n" for event in events).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self._json(404, {"error": "missing"})

    def do_POST(self):  # noqa: N802
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path == "/session":
            self._json(200, {"id": "session-123"})
        elif self.path == "/session/session-123/prompt_async":
            type(self).prompt = payload
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._json(404, {"error": "missing"})

    def log_message(self, format, *args):
        return


@pytest.fixture
def fake_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenCode)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()


@pytest.mark.parametrize("task_timeout", [0, 3])
def test_bridge_binds_session_captures_permission_and_completes(tmp_path: Path, fake_server: str, task_timeout):
    log = tmp_path / "run.jsonl"
    control = tmp_path / "run.control.json"
    secret = tmp_path / "run.secret"

    status = run_bridge(
        base_url=fake_server, password=PASSWORD, run_id="run-1", task="Do work",
        agent="coder", model="vllm/model-name", log=log, control=control,
        secret=secret, timeout=task_timeout,
    )

    assert status == 0
    descriptor = json.loads(control.read_text())
    assert descriptor == {
        "schema_version": 1, "run_id": "run-1", "session_id": "session-123",
        "base_url": fake_server, "credential_file": None, "state": "completed",
        "effective_policy": {
            "source": "opencode-server-resolved", "evaluation_order": ["global", "role:coder"],
            "global": {"bash": "ask"}, "role": {"edit": "allow"},
        },
        "role": "coder", "model": "vllm/model-name", "repository": None,
    }
    assert not secret.exists()
    events = [json.loads(line) for line in log.read_text().splitlines()]
    assert [event["type"] for event in events] == [
        "tool_use", "diagnostic", "text", "reasoning", "permission.updated", "question.asked",
    ]
    assert events[1]["part"]["kind"] == "stage-transition"
    assert events[2]["part"]["text"] == "done"
    assert "PRIVATE OTHER" not in log.read_text()
    assert "must-not-be-retained" not in control.read_text()
    assert FakeOpenCode.prompt == {
        "agent": "coder", "model": {"providerID": "vllm", "modelID": "model-name"},
        "parts": [{"type": "text", "text": "Do work"}],
    }


def test_bridge_normalizes_compaction_lifecycle_events():
    from codex_local_harness.context_controller import OpenCodeCompactionTracker

    tracker = OpenCodeCompactionTracker(
        run_id="run-1", session_id="session-123", repository="owner/repo", issue=169,
    )
    result = tracker.observe({
        "id": "event-compact",
        "type": "session.next.compaction.started",
        "properties": {
            "timestamp": 1, "sessionID": "session-123", "reason": "auto",
        },
    }, observed_at="2026-09-06T12:00:00Z")

    assert result is not None
    assert result.state == "started"
    assert result.trigger == "automatic"
    assert result.surface == "local-opencode"

    assert tracker.observe({
        "id": "event-delta",
        "type": "session.next.compaction.delta",
        "properties": {
            "timestamp": 2, "sessionID": "session-123",
            "text": "PRIVATE GENERATED SUMMARY",
        },
    }, observed_at="2026-09-06T12:00:01Z") is None
    completed = tracker.observe({
        "id": "event-ended",
        "type": "session.next.compaction.ended",
        "properties": {
            "timestamp": 3, "sessionID": "session-123",
            "text": "PRIVATE GENERATED SUMMARY", "include": "PRIVATE CONTEXT",
        },
    }, observed_at="2026-09-06T12:00:02Z")

    assert completed is not None
    assert completed.state == "completed"
    assert completed.trigger == "automatic"
    assert "PRIVATE" not in json.dumps(completed.as_dict())


def test_bridge_retains_only_content_free_opencode_compaction_records(
    tmp_path: Path, fake_server: str,
):
    from codex_local_harness.context_controller import read_compaction_records

    records = tmp_path / "compaction.jsonl"
    status = run_bridge(
        base_url=fake_server, password=PASSWORD, run_id="run-169", task="Do work",
        agent="coder", model="vllm/model-name", repository="owner/repo", issue=169,
        compaction_records=records, log=tmp_path / "run.jsonl",
        control=tmp_path / "control.json", secret=tmp_path / "secret", timeout=3,
    )
    assert status == 0
    observed = read_compaction_records(records)
    assert [(item.state, item.trigger, item.surface) for item in observed] == [
        ("started", "automatic", "local-opencode"),
        ("completed", "automatic", "local-opencode"),
    ]
    assert all(item.run_id == "run-169" and item.session_id == "session-123" for item in observed)
    assert "PRIVATE COMPACTION SUMMARY" not in records.read_text()


def test_bridge_rejects_unhealthy_endpoint(tmp_path: Path):
    with pytest.raises(BridgeError, match="did not become healthy"):
        run_bridge(
            base_url="http://127.0.0.1:1", password=PASSWORD, run_id="run-1",
            task="Do work", agent="coder", model="vllm/model", log=tmp_path / "run.jsonl",
            control=tmp_path / "control.json", secret=tmp_path / "secret", timeout=0.15,
        )


def repeated_tool_events(count, *, inputs=None, status="completed", same_id=False, session="session-123"):
    for index in range(count):
        yield {"type": "message.part.updated", "properties": {
            "sessionID": session, "part": {
                "type": "tool", "tool": "bash", "callID": "same" if same_id else str(index),
                "state": {"status": status, "input": inputs if inputs is not None else {"command": "cat README.md"}},
            },
        }}


def run_fake_events(monkeypatch, tmp_path, events, *, agent="reviewer", abort_fails=False, timeout=0):
    from codex_local_harness import opencode_bridge as bridge
    requests = []
    def request(base, password, method, path, payload=None, **kwargs):
        requests.append((method, path, kwargs))
        if path == "/global/health":
            return {"healthy": True}
        if path == "/session":
            return {"id": "session-123"}
        if path.endswith("/abort") and abort_fails:
            raise BridgeError("unavailable abort API")
        return {}
    def stream(*args):
        yield from events
        yield {"type": "session.status", "properties": {
            "sessionID": "session-123", "status": {"type": "idle"},
        }}
    monkeypatch.setattr(bridge, "_request", request)
    monkeypatch.setattr(bridge, "_events", stream)
    def run():
        return run_bridge(base_url="http://fake", password=PASSWORD, run_id="loop-test",
            task="review", agent=agent, model="vllm/model", log=tmp_path / "run.jsonl",
            control=tmp_path / "control.json", secret=tmp_path / "secret", timeout=timeout)
    return run, requests


@pytest.mark.parametrize("agent", ["reviewer", "convergence-reviewer"])
@pytest.mark.parametrize("timeout", [0, 10])
@pytest.mark.parametrize("abort_fails", [False, True])
def test_reviewer_stops_at_twentieth_completed_request(monkeypatch, tmp_path, agent, timeout, abort_fails):
    run, requests = run_fake_events(monkeypatch, tmp_path, repeated_tool_events(25),
        agent=agent, timeout=timeout, abort_fails=abort_fails)
    with pytest.raises(BridgeError, match="exploration-loop: fingerprint .*count 20"):
        run()
    assert [(method, path, options) for method, path, options in requests if path.endswith("/abort")] == [
        ("POST", "/session/session-123/abort", {"timeout": 5})]
    descriptor = json.loads((tmp_path / "control.json").read_text())
    assert descriptor["state"] == "failed"
    assert descriptor["credential_file"] is None
    cause = descriptor["termination_cause"]
    assert cause["kind"] == "exploration-loop" and cause["count"] == 20
    assert cause["reason"] and len(cause["fingerprint"]) == 16
    assert cause.get("abort_failed", False) is abort_fails
    assert not (tmp_path / "secret").exists()
    events = [json.loads(line) for line in (tmp_path / "run.jsonl").read_text().splitlines()]
    assert len([e for e in events if e["type"] == "tool_use"]) == 20
    assert any(e.get("part", {}).get("action") == "abort" for e in events)
    assert "cat README.md" not in json.dumps(cause)


@pytest.mark.parametrize("options,agent", [
    ({"count": 19}, "reviewer"),
    ({"count": 25, "inputs": {}}, "reviewer"),
    ({"count": 25, "inputs": {"command": "  ", "description": "empty"}}, "reviewer"),
    ({"count": 25, "status": "error"}, "reviewer"),
    ({"count": 25, "status": "running"}, "reviewer"),
    ({"count": 25, "same_id": True}, "reviewer"),
    ({"count": 25, "session": "other"}, "reviewer"),
    ({"count": 25}, "coder"),
    ({"count": 25}, "investigator"),
])
def test_diagnostics_do_not_imply_abort(monkeypatch, tmp_path, options, agent):
    run, requests = run_fake_events(monkeypatch, tmp_path, repeated_tool_events(**options), agent=agent)
    assert run() == 0
    assert not any(path.endswith("/abort") for _, path, _ in requests)
    assert "termination_cause" not in json.loads((tmp_path / "control.json").read_text())
    if options == {"count": 19}:
        events = [json.loads(line) for line in (tmp_path / "run.jsonl").read_text().splitlines()]
        assert any(e.get("part", {}).get("kind") == "stage-budget" for e in events)
        assert any(e.get("part", {}).get("count") == 3 and e["part"]["severity"] == "warning" for e in events)


def test_worker_cleanup_preserves_bridge_failure_and_stops_only_owned_container(tmp_path):
    import subprocess
    source = (Path(__file__).resolve().parents[1] / "bin/codex-local-worker-v4").read_text()
    start = source.index('    timeout --foreground', source.index('worker_server_pid=$!'))
    end = source.index('    (( bridge_status == 0 ))', start) + len('    (( bridge_status == 0 ))')
    control = tmp_path / "control.json"
    control.write_text(json.dumps({"state": "failed", "credential_file": "secret",
        "termination_cause": {"kind": "exploration-loop", "count": 20}}))
    (tmp_path / "secret").write_text("synthetic")
    script = '''
worker_timeout=0
candidate_source=unused
worker_server_port=1
worker_control_secret="$1/secret"
worker_run_id=run-test
worker_task_file=unused
worker_agent=reviewer
worker_model=unused
worker_repository_display=owner/repo
bridge_compaction_args=()
worker_log="$1/log"
worker_control="$1/control.json"
worker_server_container=codex-worker-run-test
stop_log="$1/stops"
timeout() { return 2; }
docker() { printf '%s\\n' "$*" >> "$stop_log"; }
sleep 0.01 &
worker_server_pid=$!
''' + source[start:end]
    result = subprocess.run(["bash", "-c", script, "test", str(tmp_path)], timeout=5)
    assert result.returncode == 1
    assert (tmp_path / "stops").read_text() == "stop --time 5 codex-worker-run-test\n"
    assert not (tmp_path / "secret").exists()
    descriptor = json.loads(control.read_text())
    assert descriptor["state"] == "failed" and descriptor["credential_file"] is None
    assert descriptor["termination_cause"] == {"kind": "exploration-loop", "count": 20}
