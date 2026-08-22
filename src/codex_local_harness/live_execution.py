from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
MAX_RUNS = 32
MAX_EVENTS_PER_RUN = 40
MAX_EVENTS = 160
MAX_NODES = 128
MAX_EDGES = 192
STALE_AFTER_SECONDS = 300
_TEST_COMMAND = re.compile(r"(^|\s|/)(pytest|tox|nox|cargo test|go test|npm test|pnpm test|yarn test)(\s|$)")


def _iso_from_millis(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _safe_result(path: Path) -> tuple[dict[str, Any] | None, bool]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, True
    return (value, False) if isinstance(value, dict) else (None, True)


def _read_events(path: Path) -> tuple[list[dict[str, Any]], int, bool]:
    """Read bounded OpenCode events without retaining prompt or output content."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return [], 1, False
    events: list[dict[str, Any]] = []
    malformed = 0
    partial_tail = False
    for index, line in enumerate(lines[-MAX_EVENTS_PER_RUN:]):
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            if index == len(lines[-MAX_EVENTS_PER_RUN:]) - 1:
                partial_tail = True
            else:
                malformed += 1
            continue
        if not isinstance(raw, dict):
            malformed += 1
            continue
        event_type = raw.get("type")
        if event_type not in {"step_start", "step_finish", "tool_use"}:
            continue
        part = raw.get("part") if isinstance(raw.get("part"), dict) else {}
        observed_at = _iso_from_millis(raw.get("timestamp"))
        normalized: dict[str, Any] = {
            "kind": event_type.replace("_", "-"),
            "observed_at": observed_at,
            "source_ref": f"v5-log:{path.name}:{max(1, len(lines) - MAX_EVENTS_PER_RUN + index + 1)}",
            "reason": "normalized retained v5 execution event",
        }
        if event_type == "tool_use":
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            tool = str(part.get("tool") or "unknown")[:48]
            status = str(state.get("status") or "unknown")[:32]
            inputs = state.get("input") if isinstance(state.get("input"), dict) else {}
            command = str(inputs.get("command") or "")
            normalized.update({
                "tool": tool,
                "status": status,
                "subject": "test" if tool == "bash" and _TEST_COMMAND.search(command) else tool,
            })
        elif event_type == "step_finish":
            normalized["status"] = str(part.get("reason") or "unknown")[:32]
        events.append(normalized)
    return events, malformed, partial_tail


def _terminal_state(result: dict[str, Any]) -> tuple[str, str]:
    if result.get("interrupted") or result.get("exit_status") in {130, 143}:
        return "interrupted", "retained result reports interruption"
    if result.get("exit_status") == 0:
        return "completed", "retained result reports successful exit"
    return "failed", "retained result reports non-zero exit"


def _live_state(events: list[dict[str, Any]], age_seconds: float) -> tuple[str, str]:
    if age_seconds > STALE_AFTER_SECONDS:
        return "stale", f"no retained event for more than {STALE_AFTER_SECONDS} seconds"
    if not events:
        return "unknown", "no normalized execution event observed"
    latest = events[-1]
    if latest["kind"] == "tool-use":
        if latest.get("subject") == "test" and latest.get("status") in {"pending", "running"}:
            return "running-tests", "recognized test command is active"
        if latest.get("status") in {"pending", "running"}:
            return "waiting-tool", f"tool {latest.get('tool', 'unknown')} is active"
        return "waiting-inference", "tool completed; awaiting the next model action"
    if latest["kind"] == "step-start":
        return "waiting-inference", "model step started and no later external action is observed"
    return "waiting-inference", "model step completed without terminal run evidence"


def project_live_execution(runs_dir: Path | None, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    empty = {
        "schema_version": SCHEMA_VERSION,
        "observed_at": now.isoformat(),
        "source_state": "unavailable" if runs_dir is None else "missing",
        "nodes": [], "edges": [], "events": [],
        "truncated": {"runs": 0, "nodes": 0, "edges": 0, "events": 0},
        "diagnostics": [],
    }
    if runs_dir is None or not runs_dir.is_dir():
        return empty

    try:
        logs = sorted(
            runs_dir.glob("*.jsonl"), key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True,
        )
    except OSError:
        return {**empty, "source_state": "malformed", "diagnostics": ["runs directory is unreadable"]}
    selected = logs[:MAX_RUNS]
    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    all_events: list[dict[str, Any]] = []
    diagnostics: list[str] = []

    for log_path in selected:
        run_id = log_path.stem
        events, malformed, partial_tail = _read_events(log_path)
        result_path = runs_dir / f"{run_id}.result.json"
        result, result_malformed = _safe_result(result_path) if result_path.exists() else (None, False)
        observed_at = max((event["observed_at"] or "" for event in events), default="")
        if not observed_at:
            observed_at = datetime.fromtimestamp(log_path.stat().st_mtime, timezone.utc).isoformat()
        age_seconds = max(0.0, now.timestamp() - log_path.stat().st_mtime)
        state, reason = _terminal_state(result) if result is not None else _live_state(events, age_seconds)
        source_ref = f"v5-result:{result_path.name}" if result is not None else f"v5-log:{log_path.name}"
        role = str((result or {}).get("role") or "worker")[:32]
        model = str((result or {}).get("model") or "unknown")[:128]
        repository = str((result or {}).get("repository") or "")[:256]
        worker_id = f"worker:{run_id}"
        nodes[worker_id] = {
            "id": worker_id, "kind": "worker", "label": f"{role} {run_id[:12]}",
            "state": state, "observed_at": observed_at, "source_ref": source_ref,
            "reason": reason, "run_id": run_id, "role": role, "model": model,
            "repository": repository or None,
            "issue": (result or {}).get("issue") if isinstance((result or {}).get("issue"), int) else None,
            "session_id": (
                str((result or {})["session_id"])[:128]
                if isinstance((result or {}).get("session_id"), str) else None
            ),
        }
        if model != "unknown":
            model_id = f"model:{model}"
            nodes[model_id] = {
                "id": model_id, "kind": "model", "label": model,
                "state": "active" if state == "waiting-inference" else state,
                "observed_at": observed_at, "source_ref": source_ref,
                "reason": f"model observed for run {run_id[:12]}",
            }
            edges[(worker_id, model_id, "inference")] = {
                "source": worker_id, "target": model_id, "kind": "inference",
                "state": state, "observed_at": observed_at, "source_ref": source_ref,
                "reason": reason,
            }
        if repository:
            repository_id = f"repository:{repository}"
            nodes[repository_id] = {
                "id": repository_id, "kind": "repository", "label": Path(repository).name,
                "state": state, "observed_at": observed_at, "source_ref": source_ref,
                "reason": f"checkout observed for run {run_id[:12]}",
            }
            edges[(worker_id, repository_id, "workspace")] = {
                "source": worker_id, "target": repository_id, "kind": "workspace",
                "state": state, "observed_at": observed_at, "source_ref": source_ref,
                "reason": "worker is associated with the retained checkout",
            }
        for event in events:
            event = {**event, "run_id": run_id}
            all_events.append(event)
            if event["kind"] != "tool-use":
                continue
            kind = "test" if event.get("subject") == "test" else "tool"
            tool_id = f"{kind}:{event.get('tool', 'unknown')}"
            nodes[tool_id] = {
                "id": tool_id, "kind": kind, "label": event.get("subject", "tool"),
                "state": event.get("status", "unknown"), "observed_at": event["observed_at"],
                "source_ref": event["source_ref"], "reason": "normalized tool event observed",
            }
            edges[(worker_id, tool_id, "uses")] = {
                "source": worker_id, "target": tool_id, "kind": "uses",
                "state": event.get("status", "unknown"), "observed_at": event["observed_at"],
                "source_ref": event["source_ref"], "reason": "worker emitted a tool event",
            }
        if malformed:
            diagnostics.append(f"{log_path.name}: {malformed} malformed non-final record(s)")
        if partial_tail:
            diagnostics.append(f"{log_path.name}: partial final record ignored")
        if result_malformed:
            diagnostics.append(f"{result_path.name}: malformed result ignored")

    ordered_nodes = sorted(nodes.values(), key=lambda item: (item["kind"], item["id"]))
    ordered_edges = sorted(edges.values(), key=lambda item: (item["kind"], item["source"], item["target"]))
    ordered_events = sorted(
        all_events, key=lambda item: (item.get("observed_at") or "", item["run_id"], item["source_ref"]),
        reverse=True,
    )
    source_state = "malformed" if any("malformed" in item for item in diagnostics) else ("partial" if diagnostics else "ok")
    return {
        "schema_version": SCHEMA_VERSION,
        "observed_at": now.isoformat(),
        "source_state": source_state,
        "nodes": ordered_nodes[:MAX_NODES],
        "edges": ordered_edges[:MAX_EDGES],
        "events": ordered_events[:MAX_EVENTS],
        "truncated": {
            "runs": max(0, len(logs) - len(selected)),
            "nodes": max(0, len(ordered_nodes) - MAX_NODES),
            "edges": max(0, len(ordered_edges) - MAX_EDGES),
            "events": max(0, len(ordered_events) - MAX_EVENTS),
        },
        "diagnostics": sorted(diagnostics),
    }


def live_fingerprint(runs_dir: Path | None) -> tuple[tuple[str, int, int], ...]:
    if runs_dir is None or not runs_dir.is_dir():
        return ()
    values = []
    try:
        paths = sorted((*runs_dir.glob("*.jsonl"), *runs_dir.glob("*.result.json")), key=lambda path: path.name)
        for path in paths[-MAX_RUNS * 2:]:
            stat = path.stat()
            values.append((path.name, stat.st_mtime_ns, stat.st_size))
    except OSError:
        return ()
    return tuple(values)
