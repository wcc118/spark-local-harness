from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from codex_local_harness.control_plane import ControlBroker, ControlError


PASSWORD = "server-secret"
TOKEN = "operator-token"


class FakeOpenCode(BaseHTTPRequestHandler):
    permissions = [
        {"id": "per_one", "sessionID": "ses_target", "permission": "bash", "patterns": ["pytest *"], "metadata": {}, "always": ["pytest *"]},
        {"id": "per_other", "sessionID": "ses_other", "permission": "bash", "patterns": ["git *"], "metadata": {}, "always": []},
    ]
    questions = [
        {"id": "que_one", "sessionID": "ses_target", "questions": [{"question": "Proceed?", "header": "Choice", "options": [{"label": "Yes", "description": "Continue"}], "multiple": False}]},
    ]
    posts: list[tuple[str, object]] = []

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
        elif self.path == "/permission":
            self._json(200, self.permissions)
        elif self.path == "/question":
            self._json(200, self.questions)
        else:
            self._json(404, {})

    def do_POST(self):  # noqa: N802
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length)) if length else None
        self.posts.append((self.path, payload))
        if self.path.endswith("/permissions/per_one"):
            self.permissions = [item for item in self.permissions if item["id"] != "per_one"]
            type(self).permissions = self.permissions
            self._json(200, True)
        elif self.path == "/question/que_one/reply":
            type(self).questions = []
            self._json(200, True)
        else:
            self._json(404, {})

    def log_message(self, format, *args):
        return


@pytest.fixture
def fake_opencode():
    FakeOpenCode.permissions = [
        {"id": "per_one", "sessionID": "ses_target", "permission": "bash", "patterns": ["pytest *"], "metadata": {}, "always": ["pytest *"]},
        {"id": "per_other", "sessionID": "ses_other", "permission": "bash", "patterns": ["git *"], "metadata": {}, "always": []},
    ]
    FakeOpenCode.questions = [
        {"id": "que_one", "sessionID": "ses_target", "questions": [{"question": "Proceed?", "header": "Choice", "options": [{"label": "Yes", "description": "Continue"}], "multiple": False}]},
    ]
    FakeOpenCode.posts = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenCode)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def broker(tmp_path: Path, base_url: str) -> ControlBroker:
    runs = tmp_path / "runs"
    runs.mkdir()
    token = tmp_path / "token"
    token.write_text(TOKEN + "\n", encoding="utf-8")
    (runs / "run-1.control.secret").write_text(PASSWORD + "\n", encoding="utf-8")
    (runs / "run-1.control.json").write_text(json.dumps({
        "schema_version": 1,
        "run_id": "run-1",
        "session_id": "ses_target",
        "base_url": base_url,
        "credential_file": str(runs / "run-1.control.secret"),
        "state": "active",
    }), encoding="utf-8")
    (runs / "run-1.jsonl").write_text(
        json.dumps({"type": "text", "part": {"text": "<script>private</script>"}}) + "\n",
        encoding="utf-8",
    )
    return ControlBroker(runs, token, tmp_path / "audit/decisions.jsonl")


def test_authenticated_snapshot_is_session_bound_and_sensitive(tmp_path, fake_opencode):
    target = broker(tmp_path, fake_opencode)
    assert target.authorized("Bearer " + TOKEN)
    assert not target.authorized("Bearer wrong")
    snapshot = target.snapshot("run-1")
    assert snapshot["session_id"] == "ses_target"
    assert [item["id"] for item in snapshot["pending_permissions"]] == ["per_one"]
    assert [item["id"] for item in snapshot["pending_questions"]] == ["que_one"]
    assert snapshot["events"][0]["part"]["text"] == "<script>private</script>"
    with pytest.raises(ControlError, match="invalid-run-id"):
        target.snapshot("../../etc/passwd")


def test_permission_decision_rejects_cross_session_and_replay(tmp_path, fake_opencode):
    target = broker(tmp_path, fake_opencode)
    with pytest.raises(ControlError, match="permission-not-pending"):
        target.decide_permission("run-1", "per_other", "once", "William")
    target.decide_permission("run-1", "per_one", "always", "William")
    assert FakeOpenCode.posts[-1] == (
        "/session/ses_target/permissions/per_one", {"response": "always"},
    )
    with pytest.raises(ControlError, match="permission-not-pending"):
        target.decide_permission("run-1", "per_one", "once", "William")
    audit = json.loads(target.audit_file.read_text(encoding="utf-8"))
    assert (audit["run_id"], audit["session_id"], audit["request_id"], audit["decision"], audit["actor"]) == (
        "run-1", "ses_target", "per_one", "always", "William",
    )


def test_question_answer_requires_exact_count_and_is_audited(tmp_path, fake_opencode):
    target = broker(tmp_path, fake_opencode)
    with pytest.raises(ControlError, match="question-answer-count-mismatch"):
        target.answer_question("run-1", "que_one", [], False, "operator")
    target.answer_question("run-1", "que_one", [["Yes"]], False, "operator")
    assert FakeOpenCode.posts[-1] == ("/question/que_one/reply", {"answers": [["Yes"]]})
    audit = json.loads(target.audit_file.read_text(encoding="utf-8"))
    assert (audit["kind"], audit["decision"]) == ("question", "answer")


def test_terminal_run_exposes_bounded_transcript_without_control(tmp_path, fake_opencode):
    target = broker(tmp_path, fake_opencode)
    control = target.runs / "run-1.control.json"
    value = json.loads(control.read_text(encoding="utf-8"))
    value.update({"state": "completed", "credential_file": None})
    control.write_text(json.dumps(value), encoding="utf-8")
    (target.runs / "run-1.control.secret").unlink()
    snapshot = target.snapshot("run-1")
    assert snapshot["state"] == "completed"
    assert snapshot["pending_permissions"] == []
    assert snapshot["pending_questions"] == []
