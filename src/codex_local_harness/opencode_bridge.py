from __future__ import annotations

import argparse
import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class BridgeError(RuntimeError):
    pass


def _request(
    base_url: str, password: str, method: str, path: str,
    payload: dict[str, Any] | None = None, *, timeout: float = 5,
) -> Any:
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(base_url + path, data=body, method=method)
    request.add_header("Authorization", "Basic " + base64.b64encode(f"opencode:{password}".encode()).decode())
    if body is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = response.read()
    except (OSError, urllib.error.HTTPError) as exc:
        raise BridgeError(f"OpenCode request failed: {method} {path}: {exc}") from exc
    if not data:
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError as exc:
        raise BridgeError(f"OpenCode returned malformed JSON: {method} {path}") from exc


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def _event_payload(raw: dict[str, Any], message_roles: dict[str, str]) -> dict[str, Any] | None:
    event_type = raw.get("type")
    properties = raw.get("properties") if isinstance(raw.get("properties"), dict) else {}
    if event_type == "message.part.updated":
        part = properties.get("part") if isinstance(properties.get("part"), dict) else {}
        part_type = str(part.get("type") or "").replace("-", "_")
        if part_type in {"text", "tool", "step_start", "step_finish"}:
            if part_type == "text" and message_roles.get(str(part.get("messageID"))) != "assistant":
                return None
            return {
                "type": "tool_use" if part_type == "tool" else part_type,
                "timestamp": int(time.time() * 1000),
                "sessionID": properties.get("sessionID") or part.get("sessionID"),
                "part": part,
            }
    if event_type in {"permission.updated", "permission.asked", "permission.replied"}:
        return {
            "type": event_type,
            "timestamp": int(time.time() * 1000),
            "properties": properties,
        }
    if event_type == "session.error":
        return {
            "type": "error", "timestamp": int(time.time() * 1000),
            "sessionID": properties.get("sessionID"), "error": properties.get("error"),
        }
    return None


def _append_event(path: Path, event: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, separators=(",", ":")) + "\n")
        handle.flush()


def _events(base_url: str, password: str, timeout: float):
    request = urllib.request.Request(base_url + "/event")
    request.add_header("Authorization", "Basic " + base64.b64encode(f"opencode:{password}".encode()).decode())
    try:
        response = urllib.request.urlopen(request, timeout=timeout)
    except (OSError, urllib.error.HTTPError) as exc:
        raise BridgeError(f"OpenCode event stream failed: {exc}") from exc
    with response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                value = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value


def run_bridge(
    *, base_url: str, password: str, run_id: str, task: str, agent: str,
    model: str, log: Path, control: Path, secret: Path, timeout: float,
) -> int:
    deadline = time.monotonic() + timeout
    while True:
        try:
            health = _request(base_url, password, "GET", "/global/health")
            if isinstance(health, dict) and health.get("healthy") is True:
                break
        except BridgeError:
            pass
        if time.monotonic() >= deadline:
            raise BridgeError("OpenCode server did not become healthy before timeout")
        time.sleep(0.1)

    session = _request(base_url, password, "POST", "/session", {"title": f"worker {run_id}"})
    session_id = session.get("id") if isinstance(session, dict) else None
    if not isinstance(session_id, str) or not session_id:
        raise BridgeError("OpenCode did not return a session id")
    secret.write_text(password + "\n", encoding="utf-8")
    os.chmod(secret, 0o600)
    _atomic_json(control, {
        "schema_version": 1, "run_id": run_id, "session_id": session_id,
        "base_url": base_url, "credential_file": str(secret), "state": "active",
    })

    try:
        provider, separator, model_id = model.partition("/")
        if not separator:
            raise BridgeError("model must use provider/model form")
        _request(base_url, password, "POST", f"/session/{session_id}/prompt_async", {
            "agent": agent,
            "model": {"providerID": provider, "modelID": model_id},
            "parts": [{"type": "text", "text": task}],
        })

        terminal_error = False
        message_roles: dict[str, str] = {}
        for raw in _events(base_url, password, max(1, deadline - time.monotonic())):
            properties = raw.get("properties") if isinstance(raw.get("properties"), dict) else {}
            part = properties.get("part") if isinstance(properties.get("part"), dict) else {}
            info = properties.get("info") if isinstance(properties.get("info"), dict) else {}
            event_session = properties.get("sessionID") or part.get("sessionID") or info.get("sessionID")
            if event_session is not None and event_session != session_id:
                continue
            if raw.get("type") == "message.updated" and isinstance(info.get("id"), str):
                message_roles[info["id"]] = str(info.get("role") or "unknown")
            normalized = _event_payload(raw, message_roles)
            if normalized is not None:
                _append_event(log, normalized)
            if raw.get("type") == "session.error":
                terminal_error = True
            if raw.get("type") == "session.status":
                status = properties.get("status") if isinstance(properties.get("status"), dict) else {}
                if status.get("type") == "idle":
                    break
            if time.monotonic() >= deadline:
                raise BridgeError("OpenCode session exceeded bridge timeout")
        else:
            raise BridgeError("OpenCode event stream ended before session completion")
    except Exception:
        descriptor = json.loads(control.read_text(encoding="utf-8"))
        descriptor["state"] = "failed"
        descriptor["credential_file"] = None
        _atomic_json(control, descriptor)
        secret.unlink(missing_ok=True)
        raise

    descriptor = json.loads(control.read_text(encoding="utf-8"))
    descriptor["state"] = "failed" if terminal_error else "completed"
    descriptor["credential_file"] = None
    _atomic_json(control, descriptor)
    secret.unlink(missing_ok=True)
    return 1 if terminal_error else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Drive one bounded worker through an OpenCode server session")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--password-file", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--task-file", type=Path, required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--secret", type=Path, required=True)
    parser.add_argument("--timeout", type=float, required=True)
    args = parser.parse_args(argv)
    try:
        password = args.password_file.read_text(encoding="utf-8").strip()
        if not password:
            raise BridgeError("OpenCode credential file is empty")
        return run_bridge(
            base_url=args.base_url, password=password, run_id=args.run_id,
            task=args.task_file.read_text(encoding="utf-8"), agent=args.agent,
            model=args.model, log=args.log, control=args.control, secret=args.secret,
            timeout=args.timeout,
        )
    except (BridgeError, OSError, ValueError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
