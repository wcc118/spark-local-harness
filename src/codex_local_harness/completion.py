"""Session-owned worker handoffs; never infers success from model prose."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_HEALTH = Path(os.environ.get(
    "CODEX_LOCAL_HEALTH_DIR", str(Path.home() / ".local/share/codex-local-harness/health"),
))
TERMINAL = {"completed", "cancelled", "timed-out", "interrupted"}


class CompletionError(ValueError):
    pass


def records(path: Path):
    if not path.exists():
        return
    with path.open(encoding="utf-8") as stream:
        for number, line in enumerate(stream, 1):
            if not line.endswith("\n"):
                raise CompletionError(f"Incomplete record in {path.name}:{number}; retry the read")
            try:
                value = json.loads(line)
            except ValueError as exc:
                raise CompletionError(f"Invalid record in {path.name}:{number}") from exc
            if not isinstance(value, dict):
                raise CompletionError(f"Non-object record in {path.name}:{number}")
            yield value


def fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def snapshot(health: Path, session: str) -> list[dict[str, Any]]:
    if not session:
        raise CompletionError("A Codex session identity is required")
    owned: dict[str, dict[str, Any]] = {}
    stages: dict[str, dict[str, str]] = {}
    for event in records(health / "workflow.jsonl"):
        if event.get("session_id") != session:
            continue
        run = event.get("run_id")
        if not isinstance(run, str) or not run:
            raise CompletionError("Owned workflow record has no run identity")
        owned[run] = event
        stages.setdefault(run, {})[str(event.get("stage"))] = str(event.get("state"))
    lifecycle = {}
    for event in records(health / "lifecycle.jsonl"):
        if event.get("run_id") in owned:
            lifecycle[event["run_id"]] = event
    acknowledgements = {
        (event.get("run_id"), event.get("fingerprint"))
        for event in records(health / "completion-acknowledgements.jsonl")
        if event.get("session_id") == session
    }
    results = []
    for run, event in owned.items():
        life = lifecycle.get(run, {})
        exit_status = life.get("exit_status")
        ended = life.get("event") in TERMINAL and type(exit_status) is int
        # Lifecycle is written before workflow finalization. Keep waiting until
        # both agree, including delivery, rather than acknowledging mid-finalize.
        settled = ended and all(s in {"passed", "failed", "interrupted"} for s in stages[run].values())
        state = "pending"
        if settled:
            state = "passed" if (
                life.get("event") == "completed" and exit_status == 0
                and all(s == "passed" for s in stages[run].values())
            ) else "failed"
        result = {
            "run_id": run, "session_id": session, "repository": event.get("repository"),
            "issue": event.get("issue"), "role": event.get("role"), "state": state,
            "exit_status": exit_status if ended else None,
            "lifecycle_event": life.get("event"), "stages": stages[run],
            "observed_at": event.get("observed_at"),
            "lifecycle_observed_at": life.get("observed_at"),
        }
        result["fingerprint"] = fingerprint(result)
        result["acknowledged"] = (run, result["fingerprint"]) in acknowledgements
        results.append(result)
    return results


def acknowledge(health: Path, session: str, run: str, disposition: str) -> dict[str, Any]:
    if not disposition.strip() or len(disposition) > 1000:
        raise CompletionError("Provide a nonempty disposition of at most 1000 characters")
    matches = [r for r in snapshot(health, session) if r["run_id"] == run]
    if not matches:
        raise CompletionError("Run does not belong to this session")
    result = matches[0]
    if result["state"] == "pending":
        raise CompletionError("Run lacks settled terminal evidence; wait or investigate the missing finalization")
    event = {"run_id": run, "session_id": session, "fingerprint": result["fingerprint"],
             "disposition": disposition, "observed_at": time.time()}
    health.mkdir(parents=True, exist_ok=True)
    # One append under a file lock; acknowledgement never edits the run evidence.
    import fcntl
    fd = os.open(health / "completion-acknowledgements.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(event) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return {**result, "acknowledged": True}


def stop_decision(health: Path, payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("hook_event_name") != "Stop":
        return {}
    session = payload.get("session_id")
    if not isinstance(session, str) or not session:
        return {"decision": "block", "reason": "Worker completion guard received no session identity; diagnose hook input."}
    pending = [r for r in snapshot(health, session) if not r["acknowledged"]]
    if not pending:
        return {}
    summary = "; ".join(f"{r['run_id']} {r['state']} exit={r['exit_status']}" for r in pending[:5])
    return {
        "decision": "block",
        "reason": (
            f"Unconsumed local worker handoff(s): {summary}. Keep this turn active. "
            "For pending runs, wait on the original exec session using write_stdin; "
            "if that handle is unavailable, run codex-local-completion status --wait 30. "
            "A yielded functions.exec must first be resumed with functions.wait. "
            "For terminal runs, inspect the result/log and GitHub state, then run "
            "codex-local-completion ack RUN_ID --disposition 'observed outcome and next action'. "
            "Acknowledgement is not approval or permission to retry. Act on the authorized next step "
            "or report the concrete failure. Never invent success or silently rerun publication. "
            "Missing terminal evidence requires investigation; inactivity is not completion. "
            "The user can interrupt or disable this hook to stop orchestration."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--health", type=Path, default=DEFAULT_HEALTH)
    parser.add_argument("--session", default=os.environ.get("CODEX_LOCAL_SESSION_ID") or os.environ.get("CODEX_THREAD_ID"))
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status")
    status.add_argument("--wait", type=int, choices=range(0, 31), default=0, metavar="0..30")
    ack = commands.add_parser("ack")
    ack.add_argument("run_id")
    ack.add_argument("--disposition", required=True)
    commands.add_parser("stop-hook")
    args = parser.parse_args(argv)
    try:
        if args.command == "stop-hook":
            payload = json.load(sys.stdin)
            if not isinstance(payload, dict):
                raise CompletionError("Hook input must be an object")
            print(json.dumps(stop_decision(args.health, payload)))
        elif args.command == "ack":
            print(json.dumps(acknowledge(args.health, args.session, args.run_id, args.disposition)))
        else:
            deadline = time.monotonic() + args.wait
            while True:
                result = snapshot(args.health, args.session)
                if not any(r["state"] == "pending" for r in result) or time.monotonic() >= deadline:
                    break
                time.sleep(min(1, max(0, deadline - time.monotonic())))
            print(json.dumps({"session_id": args.session, "runs": result}))
    except (CompletionError, OSError, ValueError) as exc:
        if args.command == "stop-hook":
            print(json.dumps({"decision": "block", "reason": f"Worker completion evidence unavailable: {exc}. Diagnose it before claiming completion."}))
        else:
            print(str(exc), file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
