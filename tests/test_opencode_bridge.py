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
            self._json(200, {"healthy": True, "version": "test"})
        elif self.path == "/config":
            self._json(200, {
                "permission": {"bash": "ask"},
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


def test_bridge_binds_session_captures_permission_and_completes(tmp_path: Path, fake_server: str):
    log = tmp_path / "run.jsonl"
    control = tmp_path / "run.control.json"
    secret = tmp_path / "run.secret"

    status = run_bridge(
        base_url=fake_server, password=PASSWORD, run_id="run-1", task="Do work",
        agent="coder", model="vllm/model-name", log=log, control=control,
        secret=secret, timeout=3,
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


def test_bridge_rejects_unhealthy_endpoint(tmp_path: Path):
    with pytest.raises(BridgeError, match="did not become healthy"):
        run_bridge(
            base_url="http://127.0.0.1:1", password=PASSWORD, run_id="run-1",
            task="Do work", agent="coder", model="vllm/model", log=tmp_path / "run.jsonl",
            control=tmp_path / "control.json", secret=tmp_path / "secret", timeout=0.15,
        )
