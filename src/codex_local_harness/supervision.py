"""Durable worker ownership and incremental, inference-free observation.

Observation never invokes inference or interprets inactivity as completion.
Execution remains attached to its caller. A retained workspace is a checkpoint,
not a resumed model session.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .completion import records


def append(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "a") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.write(json.dumps(value, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def owned(health: Path, run: str, session: str) -> dict[str, Any]:
    matches = [e for e in records(health / "ownership.jsonl") if e.get("run_id") == run]
    if len(matches) != 1 or not session or matches[0]["session_id"] != session:
        raise ValueError("Run is not uniquely owned by this session")
    return matches[0]


def register(health: Path, value: dict[str, Any]) -> None:
    for key in ("run_id", "session_id", "role", "repository"):
        if not isinstance(value.get(key), str) or not value[key]:
            raise ValueError(f"Missing {key}")
    if not re.fullmatch(r"[A-Za-z0-9._-]+", value["run_id"]):
        raise ValueError("Invalid run ID")
    # Serialize duplicate admission checks independently of the append ledger lock.
    health.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (health / ".admission.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if any(e.get("run_id") == value["run_id"] for e in records(health / "ownership.jsonl")):
            raise ValueError("Run already admitted")
        pid = os.getppid()
        append(health / "ownership.jsonl", {**value, "observed_at": time.time(),
               "pid": pid, "process_start": process_start(pid), "process_group": os.getpgid(pid)})


def process_start(pid: int) -> str:
    # Linux /proc field 22, after the parenthesized command name.
    return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]


def cancel(health: Path, run: str, session: str) -> None:
    item = owned(health, run, session)
    if any(e.get("run_id") == run for e in records(health / "execution.jsonl")):
        raise ValueError("Run already terminal")
    pid = item["pid"]
    if process_start(pid) != item["process_start"] or item["process_group"] != pid or os.getpgid(pid) != pid:
        raise ValueError("Execution identity changed or process group is not isolated; inspect before cancelling")
    append(health / "interventions.jsonl", {"run_id": run, "session_id": session,
           "kind": "cancel-requested", "observed_at": time.time()})
    os.killpg(pid, signal.SIGTERM)


def launch(command: list[str]) -> int:
    """Keep execution attached while giving cancellation its own process group."""
    process = subprocess.Popen(command, start_new_session=True)
    def forward(signum, frame):
        if process.poll() is None:
            os.killpg(process.pid, signum)
    previous = {sig: signal.signal(sig, forward) for sig in (signal.SIGTERM, signal.SIGINT)}
    try:
        health = Path(os.environ.get("CODEX_LOCAL_HEALTH_DIR", str(Path.home() / ".local/share/codex-local-harness/health")))
        item = None
        offset = 0
        log_identity = None
        while process.poll() is None:
            try:
                if item is None:
                    item = next((e for e in records(health / "ownership.jsonl") if e.get("pid") == process.pid
                                 and e.get("process_start") == process_start(process.pid)), None)
                if item:
                    log_path = Path(item["log"])
                    stat = log_path.stat()
                    identity = (stat.st_dev, stat.st_ino)
                    if log_identity is not None and identity != log_identity:
                        offset = 0  # A bounded infrastructure restart archived the prior attempt.
                    log_identity = identity
                    delta = observe(log_path, offset)
                    offset = delta["offset"]
                    if delta["events"]:
                        types = {}
                        for event in delta["events"]:
                            kind = event.get("tool_status", event["type"])
                            types[kind] = types.get(kind, 0) + 1
                        print(json.dumps({"run_id": item["run_id"], "offset": offset, "activity": types}), file=sys.stderr, flush=True)
            except (OSError, ValueError) as exc:
                # Concurrent partial appends are retryable. Never abandon the
                # execution because its optional presentation layer failed.
                print(f"Monitor evidence unavailable: {type(exc).__name__}", file=sys.stderr, flush=True)
            time.sleep(1)
        returncode = process.wait()
        return returncode if returncode >= 0 else 128 - returncode
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def finish(health: Path, run: str, session: str, status: int) -> None:
    item = owned(health, run, session)
    result_path = Path(item["log"]).with_suffix(".result.json")
    artifact = {}
    evidence_error = None
    try:
        artifact = json.loads(result_path.read_text()) if result_path.exists() else {}
        if not isinstance(artifact, dict):
            raise ValueError("Result artifact must be an object")
        result = artifact.get("result") or {}
        if not isinstance(result, dict):
            raise ValueError("Result must be an object")
    except (OSError, ValueError) as exc:
        artifact, result = {}, {}
        evidence_error = type(exc).__name__
    # Called only at the end of EXIT cleanup, after publication and projection.
    terminal = {
        "run_id": run, "session_id": session, "exit_status": status,
        "state": "terminal", "acceptance": "unknown", "observed_at": time.time(),
        "checkout": item["checkout"], "continuation": "checkpoint-restart",
        "review_verdict": result.get("verdict"), "publication": artifact.get("publication"),
        "result_evidence_error": evidence_error,
    }
    try:
        log = Path(item["log"])
        if log.exists():
            for message in records(health / "guidance.jsonl"):
                if message.get("state") != "submitted" or message.get("target_run") != run:
                    continue
                marker = "GUIDANCE_RECEIPT=" + message["guidance_id"]
                # A receipt is model-reported acknowledgement, not proof of compliance.
                received = False
                for line in log.read_text(errors="replace").splitlines():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") == "text":
                        received |= marker in str((event.get("part") or {}).get("text", ""))
                    if event.get("type") == "assistant":
                        received |= any(marker in str(c.get("text", "")) for c in
                                        (event.get("message") or {}).get("content", []) if isinstance(c, dict))
                append(health / "guidance.jsonl", {**message,
                       "state": "model-reported-receipt" if received else "receipt-unconfirmed",
                       "observed_at": time.time()})
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        terminal["guidance_evidence_error"] = type(exc).__name__
    append(health / "execution.jsonl", terminal)


def observe(log: Path, offset: int = 0, limit: int = 100) -> dict[str, Any]:
    """Read a bounded delta; partial JSON lines are retried next time.

    Export only event shape and identifiers, never shell commands or model text.
    Full raw evidence remains in the private log for deliberate inspection.
    """
    if offset < 0 or limit < 1:
        raise ValueError("Invalid observation cursor")
    events = []
    if not log.exists():
        return {"offset": offset, "events": events}
    with log.open("rb") as stream:
        if offset > log.stat().st_size:
            raise ValueError("Log shrank; observation cursor is stale")
        stream.seek(offset)
        for _ in range(limit):
            line = stream.readline()
            if not line or not line.endswith(b"\n"):
                break
            offset = stream.tell()
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("non-object")
            except (ValueError, UnicodeDecodeError):
                events.append({"type": "malformed-event", "offset": offset})
                continue
            part = event.get("part") or {}
            state = part.get("state") or {} if isinstance(part, dict) else {}
            item = {"type": event.get("type", "unknown"), "offset": offset}
            if isinstance(state, dict) and state.get("status") in {"pending", "running", "completed", "error"}:
                item["tool_status"] = state["status"]
            message = event.get("message")
            content = message.get("content", []) if isinstance(message, dict) else []
            if isinstance(content, list):
                item["tool_errors"] = sum(isinstance(c, dict) and c.get("type") == "tool_result"
                                          and c.get("is_error") is True for c in content)
            events.append(item)
    return {"offset": offset, "events": events}


def guidance(health: Path, run: str, session: str, path: Path) -> dict[str, Any]:
    item = owned(health, run, session)
    content = path.read_text()
    if not content.strip() or len(content) > 12000:
        raise ValueError("Guidance must contain 1..12000 characters")
    digest = hashlib.sha256(content.encode()).hexdigest()
    target = Path(item["log"]).parent / f"{run}.guidance-{digest}.txt"
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if target.read_text() != content:
            raise ValueError("Existing guidance evidence changed")
        prior = [e for e in records(health / "guidance.jsonl")
                 if e.get("run_id") == run and e.get("guidance_id") == digest]
        if prior:
            return prior[-1]
    else:
        with os.fdopen(fd, "w") as out:
            out.write(content)
    event = {"run_id": run, "session_id": session, "guidance_id": digest,
             "path": str(target), "state": "queued-for-checkpoint-restart",
             "observed_at": time.time()}
    append(health / "guidance.jsonl", event)
    return event


def correction(health: Path, run: str, session: str, review_path: Path) -> dict[str, Any]:
    """Route a validated exact-head review into a compact coder correction packet."""
    from .review import Envelope, ReviewError, validate_result
    item = checkpoint(health, run, session)
    artifact = json.loads(review_path.read_text())
    result = artifact.get("result")
    if not isinstance(result, dict):
        raise ValueError("Correction requires a structured finalized review artifact")
    envelope = Envelope(1, item["repository"], item["issue"], result.get("pull_request"),
                        item["checkout"], "checkpoint", item["head"], item["head"], "main")
    try:
        result = validate_result(result, envelope)
    except ReviewError as exc:
        raise ValueError(str(exc)) from exc
    if result["verdict"] != "REQUEST_CHANGES":
        raise ValueError("Only REQUEST_CHANGES can create a correction handoff")
    packet = Path(item["log"]).with_suffix(".correction.txt")
    packet.write_text("Resolve these exact-revision review findings. Preserve acceptance criteria. "
                      "If a finding conflicts with observed behavior, investigate and report evidence.\n"
                      + json.dumps({"criteria": result["criteria"], "findings": result["findings"],
                                    "tests": result["tests"]}, indent=2))
    packet.chmod(0o600)
    event = guidance(health, run, session, packet)
    append(health / "interventions.jsonl", {"run_id": run, "session_id": session,
           "kind": "local-review-correction", "guidance_id": event["guidance_id"],
           "review_head": item["head"], "observed_at": time.time()})
    return event


def checkpoint(health: Path, run: str, session: str) -> dict[str, Any]:
    item = owned(health, run, session)
    terminal = [e for e in records(health / "execution.jsonl") if e.get("run_id") == run]
    if not terminal:
        raise ValueError("Worker is active or terminal evidence is missing; do not edit its workspace")
    checkout = Path(item["checkout"])
    if not checkout.is_dir():
        raise ValueError("Retained checkout is missing")
    head = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(checkout), "status", "--porcelain=v1"], text=True)
    return {**item, "head": head, "dirty": bool(dirty), "continuation": "checkpoint-restart",
            "guidance": [e for e in records(health / "guidance.jsonl") if e.get("run_id") == run]}


def resume_packet(health: Path, run: str, session: str, target: str, task: Path, output: Path) -> None:
    item = checkpoint(health, run, session)
    # The caller must consume the original handoff before beginning another attempt.
    from .completion import snapshot
    if not next(r for r in snapshot(health, session) if r["run_id"] == run)["acknowledged"]:
        raise ValueError("Inspect and acknowledge the prior completion before checkpoint restart")
    latest = {e["guidance_id"]: e for e in item["guidance"]}
    queued = [e for e in latest.values() if e["state"] == "queued-for-checkpoint-restart"]
    packet = task.read_text() + "\n\nCONTINUATION: checkpoint restart; no prior model conversation is assumed. "
    packet += "Inspect the retained working tree and current tests before editing.\n"
    for event in queued:
        content = Path(event["path"]).read_text()
        if hashlib.sha256(content.encode()).hexdigest() != event["guidance_id"]:
            raise ValueError("Guidance evidence changed")
        packet += f"\nCODEX GUIDANCE {event['guidance_id']}:\n{content}\n"
        packet += f"Acknowledge receipt with GUIDANCE_RECEIPT={event['guidance_id']} in your response.\n"
    if len(packet) > 24000:
        raise ValueError("Continuation exceeds task packet bound; curate current guidance before restarting")
    output.write_text(packet)
    output.chmod(0o600)
    for event in queued:
        append(health / "guidance.jsonl", {**event, "state": "submitted", "target_run": target,
               "observed_at": time.time()})


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--health", type=Path, default=Path(os.environ.get(
        "CODEX_LOCAL_HEALTH_DIR", str(Path.home() / ".local/share/codex-local-harness/health"))))
    p.add_argument("--session", default=os.environ.get("CODEX_LOCAL_SESSION_ID") or os.environ.get("CODEX_THREAD_ID"))
    sub = p.add_subparsers(dest="command", required=True)
    admit = sub.add_parser("admit")
    for name in ("run-id", "repository", "role", "checkout", "log", "source"):
        admit.add_argument("--" + name, required=True)
    admit.add_argument("--issue", type=int, required=True)
    done = sub.add_parser("finish")
    done.add_argument("run_id")
    done.add_argument("--exit-status", type=int, required=True)
    resume = sub.add_parser("resume-packet")
    resume.add_argument("run_id")
    resume.add_argument("--target", required=True)
    resume.add_argument("--task", type=Path, required=True)
    resume.add_argument("--output", type=Path, required=True)
    launcher = sub.add_parser("launch")
    launcher.add_argument("argv", nargs=argparse.REMAINDER)
    for name in ("status", "watch", "guide", "checkpoint", "cancel", "correct"):
        cmd = sub.add_parser(name)
        cmd.add_argument("run_id")
        if name in {"status", "watch"}:
            cmd.add_argument("--offset", type=int, default=0)
        if name == "watch":
            cmd.add_argument("--interval", type=float, default=2)
        if name == "guide":
            cmd.add_argument("--file", type=Path, required=True)
        if name == "correct":
            cmd.add_argument("--review-result", type=Path, required=True)
    args = p.parse_args(argv)
    try:
        if args.command == "launch":
            command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
            if not command:
                raise ValueError("Missing execution command")
            return launch(command)
        elif args.command == "admit":
            register(args.health, {k: getattr(args, k) for k in
                     ("run_id", "repository", "issue", "role", "checkout", "log", "source")}
                     | {"session_id": args.session,
                        "parent_run": os.environ.get("CODEX_LOCAL_RESUME_RUN")})
        elif args.command == "finish":
            finish(args.health, args.run_id, args.session, args.exit_status)
        elif args.command == "guide":
            print(json.dumps(guidance(args.health, args.run_id, args.session, args.file)))
        elif args.command == "correct":
            print(json.dumps(correction(args.health, args.run_id, args.session, args.review_result)))
        elif args.command == "resume-packet":
            resume_packet(args.health, args.run_id, args.session, args.target, args.task, args.output)
        elif args.command == "checkpoint":
            print(json.dumps(checkpoint(args.health, args.run_id, args.session)))
        elif args.command == "cancel":
            cancel(args.health, args.run_id, args.session)
        else:
            item = owned(args.health, args.run_id, args.session)
            if args.command == "watch" and args.interval < 0.1:
                raise ValueError("Watch interval must be at least 0.1 seconds")
            while True:
                delta = observe(Path(item["log"]), args.offset)
                terminal = [e for e in records(args.health / "execution.jsonl") if e.get("run_id") == args.run_id]
                if delta["events"] or terminal or args.command == "status":
                    print(json.dumps({"run_id": args.run_id, **delta, "terminal": terminal[-1:] }), flush=True)
                args.offset = delta["offset"]
                if terminal or args.command == "status":
                    break
                time.sleep(args.interval)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        p.exit(2, f"{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
