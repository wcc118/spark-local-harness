from __future__ import annotations

import argparse
import base64
from collections import Counter
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .execution_diagnostics import ExecutionDiagnostics, load_stage_budgets
from .context_controller import (
    OpenCodeCompactionTracker,
    append_compaction_record,
    verify_opencode_compaction_contract,
)


def _iso_timestamp() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def _abort_session(base_url: str, password: str, session_id: str) -> None:
    """Abort an OpenCode session with timeout 5."""
    _request(base_url, password, "POST", f"/session/{session_id}/abort", timeout=5)


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
        if part_type in {"text", "reasoning", "tool", "step_start", "step_finish"}:
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
    if event_type in {"question.asked", "question.replied", "question.rejected"}:
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


def _events(base_url: str, password: str, timeout: float | None):
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
    model: str, repository: str | None = None, issue: int | None = None,
    compaction_records: Path | None = None, log: Path, control: Path,
    secret: Path, timeout: float,
) -> int:
    started = time.monotonic()
    arguments = dict(base_url=base_url, password=password, run_id=run_id, task=task,
                     agent=agent, model=model, repository=repository, issue=issue,
                     compaction_records=compaction_records, log=log, control=control,
                     secret=secret, timeout=timeout)
    try:
        return _run_bridge_once(**arguments)
    except BridgeError as exc:
        eligible = (os.environ.get("CODEX_LOCAL_REVIEW_RESULT_MODE") == "authoritative"
                    and agent in {"reviewer", "convergence-reviewer"}
                    and str(exc) == "OpenCode event stream ended before session completion")
        if not eligible:
            raise
        remaining = timeout - (time.monotonic() - started) if timeout > 0 else timeout
        if timeout > 0 and remaining <= 0:
            raise
        descriptor = json.loads(control.read_text(encoding="utf-8"))
        session_id = descriptor.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            raise BridgeError("Cannot restart reviewer without prior session identity") from exc
        # Reviewer checkpoint resume is unsupported. Abort before one fresh session.
        # A failed abort cannot safely create a second active reviewer.
        _abort_session(base_url, password, session_id)
        archive = log.with_name(log.name + ".attempt-1")
        archived_control = control.with_name(control.name + ".attempt-1")
        if archive.exists() or archived_control.exists():
            raise BridgeError("Reviewer restart evidence already exists") from exc
        if log.exists():
            log.rename(archive)
        else:
            archive.write_text("", encoding="utf-8")
        _atomic_json(archived_control, descriptor)
        _append_event(log, {"type": "diagnostic", "part": {
            "kind": "infrastructure-restart", "attempt": 1,
            "reason": str(exc), "prior_log": str(archive),
            "prior_session": session_id, "resume_supported": False}})
        arguments["timeout"] = remaining
        # No recursion: a second stream loss is terminal, never another retry.
        return _run_bridge_once(**arguments)


def _run_bridge_once(
    *, base_url: str, password: str, run_id: str, task: str, agent: str,
    model: str, repository: str | None = None, issue: int | None = None,
    compaction_records: Path | None = None, log: Path, control: Path,
    secret: Path, timeout: float,
) -> int:
    deadline = time.monotonic() + timeout if timeout > 0 else float("inf")
    startup_deadline = min(deadline, time.monotonic() + 30)
    diagnostics = ExecutionDiagnostics(agent, load_stage_budgets(os.environ.get("CODEX_LOCAL_STAGE_BUDGETS")))
    if (issue is None) != (compaction_records is None):
        raise BridgeError("issue and compaction records must be configured together")
    if issue is not None and repository is None:
        raise BridgeError("compaction recording requires a repository")
    while True:
        try:
            health = _request(base_url, password, "GET", "/global/health")
            if isinstance(health, dict) and health.get("healthy") is True:
                break
        except BridgeError:
            pass
        if time.monotonic() >= startup_deadline:
            raise BridgeError("OpenCode server did not become healthy before timeout")
        time.sleep(0.1)

    session = _request(base_url, password, "POST", "/session", {"title": f"worker {run_id}"})
    session_id = session.get("id") if isinstance(session, dict) else None
    if not isinstance(session_id, str) or not session_id:
        raise BridgeError("OpenCode did not return a session id")
    compaction_tracker = None
    if issue is not None and compaction_records is not None and repository is not None:
        compaction_tracker = OpenCodeCompactionTracker(
            run_id=run_id, session_id=session_id, repository=repository, issue=issue,
        )
    try:
        resolved_config = _request(base_url, password, "GET", "/config")
    except BridgeError:
        resolved_config = None
    global_policy = resolved_config.get("permission") if isinstance(resolved_config, dict) else None
    agents = resolved_config.get("agent") if isinstance(resolved_config, dict) else None
    role_config = agents.get(agent) if isinstance(agents, dict) and isinstance(agents.get(agent), dict) else {}
    role_policy = role_config.get("permission") if isinstance(role_config, dict) else None
    effective_policy = {
        "source": "opencode-server-resolved" if isinstance(resolved_config, dict) else "unavailable",
        "evaluation_order": ["global", f"role:{agent}"],
        "global": global_policy if isinstance(global_policy, dict) else {},
        "role": role_policy if isinstance(role_policy, dict) else {},
    }
    if compaction_records is not None:
        version = health.get("version") if isinstance(health, dict) else None
        compaction = resolved_config.get("compaction") if isinstance(resolved_config, dict) else None
        try:
            verify_opencode_compaction_contract(
                version=version if isinstance(version, str) else "",
                compaction=compaction if isinstance(compaction, dict) else {},
            )
        except ValueError as exc:
            raise BridgeError(str(exc)) from exc
    if len(json.dumps(effective_policy)) > 65536:
        effective_policy = {"source": "oversized", "evaluation_order": [], "global": {}, "role": {}}
    secret.write_text(password + "\n", encoding="utf-8")
    os.chmod(secret, 0o600)
    _atomic_json(control, {
        "schema_version": 1, "run_id": run_id, "session_id": session_id,
        "base_url": base_url, "credential_file": str(secret), "state": "active",
        "role": agent, "model": model, "repository": repository,
        "effective_policy": effective_policy,
    })

    termination_cause = None
    completed_calls: set[str] = set()
    completed_requests: Counter[str] = Counter()
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
        for raw in _events(base_url, password, max(1, deadline - time.monotonic()) if timeout > 0 else None):
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
                for diagnostic in diagnostics.observe(normalized):
                    _append_event(log, diagnostic)
                # Enforcement is separate from the existing observational diagnostics.
                if agent in {"reviewer", "convergence-reviewer"} and normalized["type"] == "tool_use":
                    tool_part = normalized["part"]
                    state = tool_part.get("state") or {}
                    inputs = state.get("input") or {}
                    call_id = tool_part.get("callID") or tool_part.get("id")
                    nonempty = isinstance(inputs, dict) and bool(inputs)
                    if tool_part.get("tool") == "bash":
                        command = inputs.get("command") if isinstance(inputs, dict) else None
                        nonempty = isinstance(command, str) and bool(command.strip())
                    if call_id and state.get("status") == "completed" and call_id not in completed_calls:
                        completed_calls.add(call_id)
                        if nonempty:
                            fingerprint = ExecutionDiagnostics._fingerprint(tool_part)
                            completed_requests[fingerprint] += 1
                            count = completed_requests[fingerprint]
                            if count >= 20:
                                termination_cause = {
                                    "kind": "exploration-loop", "fingerprint": fingerprint,
                                    "count": count,
                                    "reason": "reviewer repeated the same nonempty completed request 20 times",
                                }
                                _append_event(log, {
                                    "type": "diagnostic", "timestamp": int(time.time() * 1000),
                                    "part": {**termination_cause, "severity": "critical", "action": "abort"},
                                })
                                try:
                                    _abort_session(base_url, password, session_id)
                                except BridgeError:
                                    termination_cause["abort_failed"] = True
                                raise BridgeError(
                                    f"exploration-loop: fingerprint {fingerprint}, count {count}"
                                )
            if compaction_tracker is not None:
                compaction = compaction_tracker.observe(raw, observed_at=_iso_timestamp())
                if compaction is not None:
                    append_compaction_record(compaction_records, compaction)
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
        if termination_cause is not None:
            descriptor["termination_cause"] = termination_cause
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
    parser.add_argument("--repository")
    parser.add_argument("--issue", type=int)
    parser.add_argument("--compaction-records", type=Path)
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
            model=args.model, repository=args.repository, issue=args.issue,
            compaction_records=args.compaction_records, log=args.log,
            control=args.control, secret=args.secret, timeout=args.timeout,
        )
    except (BridgeError, OSError, ValueError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
