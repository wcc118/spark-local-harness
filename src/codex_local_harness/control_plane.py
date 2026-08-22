from __future__ import annotations

import base64
import fnmatch
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
REQUEST_ID = re.compile(r"^(?:per|que)[A-Za-z0-9_-]{1,125}$")
MAX_EVENTS = 500
MAX_TRANSCRIPT_BYTES = 2 * 1024 * 1024


class ControlError(RuntimeError):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("expected JSON object")
    return value


def _opencode_request(
    base_url: str, password: str, method: str, path: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(base_url + path, data=body, method=method)
    credential = base64.b64encode(f"opencode:{password}".encode()).decode()
    request.add_header("Authorization", f"Basic {credential}")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        raise ControlError(409 if exc.code in {404, 409} else 502, "opencode-rejected") from exc
    except OSError as exc:
        raise ControlError(502, "opencode-unavailable") from exc
    if not data:
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        raise ControlError(502, "opencode-malformed-response") from exc


class ControlBroker:
    def __init__(self, runs: Path, token_file: Path, audit_file: Path):
        self.runs = runs
        self.token_file = token_file
        self.audit_file = audit_file

    def authorized(self, authorization: str | None) -> bool:
        try:
            expected = self.token_file.read_text(encoding="utf-8").strip()
        except OSError:
            return False
        supplied = ""
        if authorization and authorization.startswith("Bearer "):
            supplied = authorization[7:]
        return bool(expected) and hmac.compare_digest(supplied, expected)

    def _paths(self, run_id: str) -> tuple[Path, Path, Path]:
        if not RUN_ID.fullmatch(run_id):
            raise ControlError(400, "invalid-run-id")
        return (
            self.runs / f"{run_id}.control.json",
            self.runs / f"{run_id}.control.secret",
            self.runs / f"{run_id}.jsonl",
        )

    def _active(self, run_id: str) -> tuple[dict[str, Any], str]:
        control_path, secret_path, _ = self._paths(run_id)
        try:
            control = _read_json(control_path)
            password = secret_path.read_text(encoding="utf-8").strip()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ControlError(409, "run-not-controllable") from exc
        if control.get("run_id") != run_id or control.get("state") != "active":
            raise ControlError(409, "run-not-active")
        session_id = control.get("session_id")
        base_url = control.get("base_url")
        credential_file = control.get("credential_file")
        if not isinstance(session_id, str) or not session_id.startswith("ses_"):
            raise ControlError(409, "invalid-session-binding")
        if not isinstance(base_url, str) or not base_url.startswith("http://127.0.0.1:"):
            raise ControlError(409, "invalid-server-binding")
        if credential_file != str(secret_path) or not password:
            raise ControlError(409, "invalid-credential-binding")
        return control, password

    def _pending(self, control: dict[str, Any], password: str, kind: str) -> list[dict[str, Any]]:
        endpoint = "/permission" if kind == "permission" else "/question"
        value = _opencode_request(str(control["base_url"]), password, "GET", endpoint)
        if not isinstance(value, list):
            raise ControlError(502, "invalid-pending-response")
        return [item for item in value if isinstance(item, dict) and item.get("sessionID") == control["session_id"]]

    def snapshot(self, run_id: str) -> dict[str, Any]:
        control_path, _, log_path = self._paths(run_id)
        try:
            control = _read_json(control_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ControlError(404, "run-not-found") from exc
        if control.get("run_id") != run_id:
            raise ControlError(409, "invalid-run-binding")
        events: list[dict[str, Any]] = []
        raw_events: list[str] = []
        size = 0
        try:
            with log_path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - MAX_TRANSCRIPT_BYTES))
                raw_events = handle.read(MAX_TRANSCRIPT_BYTES).decode("utf-8", errors="replace").splitlines()
                if size > MAX_TRANSCRIPT_BYTES and raw_events:
                    raw_events = raw_events[1:]
            for raw in raw_events[-MAX_EVENTS:]:
                value = json.loads(raw)
                if isinstance(value, dict):
                    events.append(value)
        except FileNotFoundError:
            pass
        except json.JSONDecodeError as exc:
            raise ControlError(409, "malformed-transcript") from exc
        permissions: list[dict[str, Any]] = []
        questions: list[dict[str, Any]] = []
        if control.get("state") == "active":
            active, password = self._active(run_id)
            permissions = self._pending(active, password, "permission")
            questions = self._pending(active, password, "question")
        effective_policy = control.get("effective_policy") or {
            "source": "unavailable", "evaluation_order": [], "global": {}, "role": {},
        }
        permissions = [self._with_policy_evaluation(item, effective_policy) for item in permissions]
        return {
            "schema_version": 1,
            "run_id": run_id,
            "session_id": control.get("session_id"),
            "state": control.get("state"),
            "events": events,
            "events_truncated": len(raw_events) > MAX_EVENTS or size > MAX_TRANSCRIPT_BYTES,
            "pending_permissions": permissions,
            "pending_questions": questions,
            "effective_policy": effective_policy,
            "sensitive": True,
        }

    @staticmethod
    def _with_policy_evaluation(request: dict[str, Any], policy: dict[str, Any]) -> dict[str, Any]:
        action = request.get("permission")
        resources = request.get("patterns") if isinstance(request.get("patterns"), list) else []
        evaluations = []
        for resource in resources:
            winner = None
            if not isinstance(resource, str) or not isinstance(action, str):
                continue
            for layer_name in ("global", "role"):
                layer = policy.get(layer_name)
                if not isinstance(layer, dict):
                    continue
                rules = layer.get(action, layer.get("*"))
                if isinstance(rules, str):
                    winner = {"layer": layer_name, "pattern": action if action in layer else "*", "effect": rules}
                elif isinstance(rules, dict):
                    for pattern, effect in rules.items():
                        if isinstance(pattern, str) and isinstance(effect, str) and fnmatch.fnmatchcase(resource, pattern):
                            winner = {"layer": layer_name, "pattern": pattern, "effect": effect}
            evaluations.append({"resource": resource, "winner": winner})
        enriched = dict(request)
        enriched["policy_evaluation"] = evaluations
        return enriched

    def decide_permission(self, run_id: str, request_id: str, decision: str, actor: str) -> None:
        if not REQUEST_ID.fullmatch(request_id) or not request_id.startswith("per"):
            raise ControlError(400, "invalid-permission-id")
        if decision not in {"once", "always", "reject"}:
            raise ControlError(400, "invalid-permission-decision")
        control, password = self._active(run_id)
        pending = self._pending(control, password, "permission")
        if not any(item.get("id") == request_id for item in pending):
            raise ControlError(409, "permission-not-pending")
        path = f"/session/{control['session_id']}/permissions/{request_id}"
        _opencode_request(str(control["base_url"]), password, "POST", path, {"response": decision})
        self._audit(run_id, control, request_id, "permission", decision, actor)

    def answer_question(
        self, run_id: str, request_id: str, answers: list[list[str]] | None,
        reject: bool, actor: str,
    ) -> None:
        if not REQUEST_ID.fullmatch(request_id) or not request_id.startswith("que"):
            raise ControlError(400, "invalid-question-id")
        control, password = self._active(run_id)
        pending = self._pending(control, password, "question")
        request = next((item for item in pending if item.get("id") == request_id), None)
        if request is None:
            raise ControlError(409, "question-not-pending")
        if reject:
            path = f"/question/{request_id}/reject"
            payload = None
            decision = "reject"
        else:
            if not isinstance(answers, list) or not all(
                isinstance(answer, list) and answer and all(isinstance(label, str) and 0 < len(label) <= 256 for label in answer)
                for answer in answers
            ):
                raise ControlError(400, "invalid-question-answers")
            if len(answers) != len(request.get("questions") or []):
                raise ControlError(400, "question-answer-count-mismatch")
            path = f"/question/{request_id}/reply"
            payload = {"answers": answers}
            decision = "answer"
        _opencode_request(str(control["base_url"]), password, "POST", path, payload)
        self._audit(run_id, control, request_id, "question", decision, actor)

    def _audit(
        self, run_id: str, control: dict[str, Any], request_id: str,
        kind: str, decision: str, actor: str,
    ) -> None:
        record = {
            "schema_version": 1,
            "audit_id": f"{run_id}:{request_id}:{time.time_ns()}",
            "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "run_id": run_id,
            "session_id": control["session_id"],
            "request_id": request_id,
            "kind": kind,
            "decision": decision,
            "actor": actor[:128],
        }
        self.audit_file.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.audit_file, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(descriptor, (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode())
        finally:
            os.close(descriptor)
