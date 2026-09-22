#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .lifecycle import LifecycleCase, all_cases


CONTRACT = """WORKFLOW SPECIALIST CONTRACT: Treat the structured issue, investigation state, implementation state, review findings, and verification evidence as gates. Select the single safe next-state label supported by all supplied facts. Do not implement, mutate GitHub, or invent missing evidence."""


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def prompt(case: LifecycleCase, workspace: Path) -> str:
    return f"""{CONTRACT}

# Structured managed-lifecycle issue

## Objective
Choose the safe next workflow state for this bounded issue.

## Context and current state
Read {workspace / 'evidence.json'}.

## Scope
Analyze only the supplied lifecycle evidence.

## Acceptance criteria
Return exactly one allowed label supported by the evidence.

## Constraints and exclusions
Do not modify anything except {workspace / 'result.json'}. Do not perform optional investigation.

## Required verification
Check requirements completeness, routing readiness, review blockers, and observed test evidence before deciding.

## Durable deliverable
Write exactly {{"answers":["ALLOWED_LABEL"],"evidence":["brief observation"]}} to {workspace / 'result.json'}.

## Escalation conditions
Use the matching escalation/evidence label when safe continuation is not supported. Stop after writing valid JSON.
"""


def run_case(case: LifecycleCase, args: argparse.Namespace, repeat: int) -> dict[str, Any]:
    identity = digest({"case": case.case_id, "evidence": case.evidence, "expected": case.expected, "contract": CONTRACT})
    slug = digest({"model": args.model, "case": case.case_id, "repeat": repeat})[:12]
    attempt_dir = args.output / "attempts" / slug / case.case_id
    attempt_file = attempt_dir / "attempt.json"
    if args.resume and attempt_file.exists():
        old = json.loads(attempt_file.read_text())
        if old.get("identity_hash") != identity:
            raise ValueError(f"identity mismatch for {case.case_id}")
        return old
    if attempt_dir.exists():
        shutil.rmtree(attempt_dir)
    workspace = attempt_dir / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "evidence.json").write_text(json.dumps(case.evidence, indent=2, sort_keys=True) + "\n")
    command = [args.opencode, "run", "--format", "json", "--model", args.model, prompt(case, workspace)]
    started = time.monotonic()
    raw = ""
    code = None
    state = "failed"
    try:
        completed = subprocess.run(command, cwd=workspace, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, timeout=args.timeout or case.timeout)
        raw, code = completed.stdout or "", completed.returncode
        state = "completed" if code == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        value = exc.stdout or ""
        raw = value.decode(errors="replace") if isinstance(value, bytes) else value
        state = "timed-out"
    elapsed = time.monotonic() - started
    answers: list[str] = []
    valid = False
    try:
        result = json.loads((workspace / "result.json").read_text())
        answers = result.get("answers", [])
        valid = isinstance(answers, list) and all(isinstance(item, str) for item in answers)
    except (OSError, ValueError, TypeError):
        pass
    correct = valid and set(answers) == set(case.expected)
    calls, errors, tokens = 0, 0, 0
    seen: set[str] = set()
    for line in raw.splitlines():
        try:
            part = json.loads(line).get("part", {})
        except (ValueError, AttributeError):
            continue
        if part.get("type") == "tool":
            call = str(part.get("callID") or part.get("id") or len(seen))
            seen.add(call)
            errors += (part.get("state") or {}).get("status") == "error"
        if isinstance(part.get("tokens"), dict):
            tokens += int(part["tokens"].get("total") or 0)
    calls = len(seen)
    record = {
        "schema_version": 1, "track": "lifecycle", "case_id": case.case_id,
        "model": args.model, "route": args.route, "policy": "managed-lifecycle-v1",
        "repeat": repeat, "identity_hash": identity, "state": state,
        "artifact_correct": correct, "completed_and_correct": state == "completed" and correct,
        "answers": sorted(answers), "elapsed_seconds": round(elapsed, 3),
        "tool_calls": calls, "tool_errors": errors, "tokens_total": tokens,
        "worker_exit_code": code, "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    attempt_dir.mkdir(parents=True, exist_ok=True)
    (attempt_dir / "events.jsonl").write_text(raw)
    attempt_file.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "attempts": len(rows),
        "artifact_correct": sum(bool(row["artifact_correct"]) for row in rows),
        "completed_and_correct": sum(bool(row["completed_and_correct"]) for row in rows),
        "timeouts": sum(row["state"] == "timed-out" for row in rows),
        "elapsed_seconds": round(sum(float(row["elapsed_seconds"]) for row in rows), 3),
        "tool_calls": sum(int(row["tool_calls"]) for row in rows),
        "tool_errors": sum(int(row["tool_errors"]) for row in rows),
        "tokens_total": sum(int(row["tokens_total"]) for row in rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Frozen managed-lifecycle benchmark")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--opencode", default="opencode")
    parser.add_argument("--model", required=True)
    parser.add_argument("--route", required=True)
    parser.add_argument("--case", action="append")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    selected = [case for case in all_cases() if not args.case or case.case_id in set(args.case)]
    if not selected:
        parser.error("no cases selected")
    rows = [run_case(case, args, repeat) for case in selected for repeat in range(1, args.repeat + 1)]
    print(json.dumps(aggregate(rows), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
