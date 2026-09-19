#!/usr/bin/env python3
"""Execute frozen role-suite v2 cases through OpenCode."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cases import Case, all_cases

POLICIES = {
    "neutral": "Perform the bounded task and write result.json.",
    "structured": ("Use four phases: inspect the supplied evidence, derive only supported conclusions, "
                   "write result.json, then stop immediately. Do not perform optional investigation or repeat checks."),
}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def identity(case: Case, policy: str) -> str:
    public = {"case_id": case.case_id, "suite": case.suite, "category": case.category,
              "evidence": case.evidence, "timeout": case.timeout}
    private = {"expected": case.expected, "forbidden": case.forbidden}
    return digest({"public": public, "oracle": private, "policy": POLICIES[policy]})


def prompt(case: Case, policy: str, workspace: Path) -> str:
    evidence_path = workspace / "evidence.json"
    result_path = workspace / "result.json"
    return f"""{POLICIES[policy]}

Read {evidence_path}. This is a read-only analysis task: do not modify or create anything except {result_path}.

Return exactly one JSON object in {result_path} with this schema:
{{"answers": ["ALLOWED_LABEL", "..."], "evidence": ["brief supporting observation", "..."]}}

Use only labels listed in evidence.json. Include every materially supported label and no unsupported label. An empty answers list is valid. Stop immediately after writing valid result.json.
"""


def normalize_events(raw: str) -> tuple[int, int, int]:
    calls: set[str] = set(); errors: set[str] = set(); tokens = 0
    for line in raw.splitlines():
        try: event = json.loads(line)
        except json.JSONDecodeError: continue
        part = event.get("part", {}) if isinstance(event, dict) else {}
        if isinstance(part, dict) and part.get("type") == "tool":
            cid = str(part.get("callID") or part.get("id") or len(calls)); calls.add(cid)
            if (part.get("state") or {}).get("status") == "error": errors.add(cid)
        tok = part.get("tokens") if isinstance(part, dict) else None
        if isinstance(tok, dict): tokens += int(tok.get("total") or 0)
    return len(calls), len(errors), tokens


def score(case: Case, workspace: Path, before: dict[str, str]) -> tuple[bool, dict[str, Any]]:
    result_path = workspace / "result.json"
    try:
        result = json.loads(result_path.read_text())
        answers = result["answers"]
        valid = isinstance(answers, list) and all(isinstance(x, str) for x in answers)
    except (OSError, ValueError, KeyError, TypeError):
        return False, {"reason": "missing-or-invalid-result"}
    after = {p.relative_to(workspace).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in workspace.rglob("*") if p.is_file() and p.name != "result.json"}
    mutated = after != before
    got = set(answers)
    correct = valid and not mutated and got == set(case.expected) and not (got & set(case.forbidden))
    return correct, {"answers": sorted(got), "expected_count": len(case.expected), "workspace_mutated": mutated}


def run_case(case: Case, args: argparse.Namespace, repeat: int) -> dict[str, Any]:
    ident = identity(case, args.policy)
    slug = hashlib.sha256(f"{args.model}:{args.policy}:r{repeat}".encode()).hexdigest()[:12]
    attempt_dir = args.output / "attempts" / slug / case.case_id
    attempt_file = attempt_dir / "attempt.json"
    if args.resume and attempt_file.exists():
        old = json.loads(attempt_file.read_text())
        if old.get("identity_hash") != ident: raise ValueError(f"identity mismatch for {case.case_id}")
        return old
    workspace = attempt_dir / "workspace"
    if attempt_dir.exists(): shutil.rmtree(attempt_dir)
    workspace.mkdir(parents=True)
    (workspace / "evidence.json").write_text(json.dumps(case.evidence, indent=2, sort_keys=True) + "\n")
    before = {"evidence.json": hashlib.sha256((workspace / "evidence.json").read_bytes()).hexdigest()}
    timeout = args.timeout or case.timeout
    cmd = [args.opencode, "run", "--format", "json", "--model", args.model,
           prompt(case, args.policy, workspace)]
    started = time.monotonic(); raw = ""; exit_code = None; state = "failed"
    try:
        proc = subprocess.run(cmd, cwd=workspace, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=timeout)
        raw = proc.stdout or ""; exit_code = proc.returncode
        state = "completed" if exit_code == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        raw_value = exc.stdout or ""
        raw = raw_value.decode(errors="replace") if isinstance(raw_value, bytes) else raw_value
        state = "timed-out"
    elapsed = time.monotonic() - started
    correct, oracle = score(case, workspace, before)
    calls, errors, tokens = normalize_events(raw)
    (attempt_dir / "events.jsonl").write_text(raw)
    record = {"schema_version": 2, "case_id": case.case_id, "suite": case.suite,
              "category": case.category, "model": args.model, "route": args.route,
              "policy": args.policy, "repeat": repeat, "identity_hash": ident,
              "state": state, "terminal_completed": state == "completed",
              "artifact_correct": correct, "completed_and_correct": state == "completed" and correct,
              "elapsed_seconds": round(elapsed, 3), "tool_calls": calls, "tool_errors": errors,
              "tokens_total": tokens, "worker_exit_code": exit_code, "oracle": oracle,
              "observed_at": datetime.now(timezone.utc).isoformat()}
    attempt_file.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def selected(args: argparse.Namespace) -> list[Case]:
    cases = all_cases()
    if args.suite: cases = [c for c in cases if c.suite == args.suite]
    if args.case: cases = [c for c in cases if c.case_id in set(args.case)]
    return cases


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {"attempts": len(records), "artifact_correct": sum(bool(x["artifact_correct"]) for x in records),
            "completed_and_correct": sum(bool(x["completed_and_correct"]) for x in records),
            "timeouts": sum(x["state"] == "timed-out" for x in records),
            "elapsed_seconds": round(sum(x["elapsed_seconds"] for x in records), 3),
            "tool_calls": sum(x["tool_calls"] for x in records),
            "tool_errors": sum(x["tool_errors"] for x in records),
            "tokens_total": sum(x["tokens_total"] for x in records)}


def main() -> int:
    p = argparse.ArgumentParser(); sub = p.add_subparsers(dest="command", required=True)
    v = sub.add_parser("verify")
    r = sub.add_parser("run")
    r.add_argument("--output", type=Path, required=True); r.add_argument("--opencode", required=True)
    r.add_argument("--model", required=True); r.add_argument("--route", required=True)
    r.add_argument("--policy", choices=POLICIES, default="neutral"); r.add_argument("--suite")
    r.add_argument("--case", action="append"); r.add_argument("--repeat", type=int, default=1)
    r.add_argument("--timeout", type=int); r.add_argument("--resume", action="store_true")
    a = p.parse_args()
    if a.command == "verify":
        cases = all_cases(); counts = {s: sum(c.suite == s for c in cases) for s in sorted({c.suite for c in cases})}
        print(json.dumps({"cases": len(cases), "counts": counts, "unique": len({c.case_id for c in cases}) == len(cases)})); return 0
    records = [run_case(c, a, a.repeat) for c in selected(a)]
    print(json.dumps(aggregate(records), sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
