"""Scalar Codex usage for explicitly bracketed assessment intervals.

An interval measures the calling session, including any concurrent work. It is
not automatic attribution to monitoring and must not be summed across overlaps.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .operation_usage import TOKEN_COUNT_DISCRIMINATOR, find_rollout
from .supervision import append, owned
from .completion import records


FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens",
          "reasoning_output_tokens", "total_tokens")


def counters(root: Path, session: str) -> dict:
    latest = None
    with find_rollout(root, session).open() as stream:
        for line in stream:
            if not TOKEN_COUNT_DISCRIMINATOR.search(line):
                continue
            event = json.loads(line)
            payload = event.get("payload") or {}
            if payload.get("type") != "token_count":
                continue
            usage = (payload.get("info") or {}).get("total_token_usage")
            if not isinstance(usage, dict):
                continue
            values = {name: usage.get(name) for name in FIELDS}
            if any(type(v) is not int or v < 0 for v in values.values()):
                raise ValueError("Invalid or missing cumulative token counter")
            latest = {"source_timestamp": event.get("timestamp"), "counters": values}
    if latest is None:
        raise ValueError("No cumulative token counters available")
    return latest


def capture(health: Path, root: Path, session: str, run: str,
            assessment: str, phase: str, kind: str) -> dict:
    item = owned(health, run, session)
    if not assessment or len(assessment) > 120:
        raise ValueError("Assessment ID must contain 1..120 characters")
    if phase not in {"start", "finish"} or kind not in {"monitoring", "guidance", "review", "recovery"}:
        raise ValueError("Invalid assessment phase or kind")
    path = health / "assessments.jsonl"
    # One command per assessment transition; serialize admission and append.
    import fcntl
    with (health / ".assessment.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        prior = [e for e in records(path) if e.get("session_id") == session
                 and e.get("assessment_id") == assessment]
        if any(e["phase"] == phase for e in prior):
            raise ValueError("Assessment phase already captured; use its retained record")
        if prior and any(e["run_id"] != run or e["kind"] != kind for e in prior):
            raise ValueError("Assessment identity changed")
        value = {"session_id": session, "run_id": run, "assessment_id": assessment,
                 "repository": item["repository"], "issue": item["issue"],
                 "phase": phase, "kind": kind, "captured_at": time.time(),
                 "attribution": "whole-session-interval; may include concurrent work",
                 "status": "unknown", "delta": None}
        try:
            value.update(counters(root, session))
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            value["diagnostic"] = type(exc).__name__
        if phase == "finish":
            start = next((e for e in prior if e["phase"] == "start"), {})
            if "counters" in start and "counters" in value:
                delta = {k: value["counters"][k] - start["counters"][k] for k in FIELDS}
                if min(delta.values()) >= 0 and delta["cached_input_tokens"] <= delta["input_tokens"]:
                    value.update(status="observed", delta=delta)
                else:
                    value["diagnostic"] = "counter-reset-or-inconsistent-cache"
            else:
                value.setdefault("diagnostic", "missing-start-or-counters")
        append(path, value)
        return value


def main(argv=None):
    import os
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("phase", choices=["start", "finish"])
    p.add_argument("--run", required=True)
    p.add_argument("--assessment", required=True)
    p.add_argument("--kind", choices=["monitoring", "guidance", "review", "recovery"], required=True)
    p.add_argument("--health", type=Path, default=Path(os.environ.get(
        "CODEX_LOCAL_HEALTH_DIR", str(Path.home() / ".local/share/codex-local-harness/health"))))
    p.add_argument("--session-root", type=Path, default=Path.home() / ".codex/sessions")
    p.add_argument("--session", default=os.environ.get("CODEX_LOCAL_SESSION_ID") or os.environ.get("CODEX_THREAD_ID"))
    args = p.parse_args(argv)
    try:
        print(json.dumps(capture(args.health, args.session_root, args.session,
                                 args.run, args.assessment, args.phase, args.kind)))
    except (OSError, ValueError) as exc:
        p.exit(2, f"{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
