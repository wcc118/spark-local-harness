"""Canonical candidate accounting. Unknown usage is never represented as zero."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .supervision import append
from .completion import records


def count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def normalize(log: Path, runtime: str, exit_status: int | None = None) -> dict[str, Any]:
    events = []
    malformed = False
    for line in log.read_text(errors="replace").splitlines() if log.exists() else []:
        try:
            event = json.loads(line)
            if isinstance(event, dict):
                events.append(event)
            else:
                malformed = True
        except ValueError:
            malformed = True
    restarted = any(e.get("type") == "diagnostic" and
                    (e.get("part") or {}).get("kind") == "infrastructure-restart" for e in events)
    calls, errors = set(), set()
    observed: dict[str, dict[str, Any]] = {}
    result = None
    for index, event in enumerate(events):
        if runtime == "qwen-code":
            message = event.get("message") or {}
            for n, part in enumerate(message.get("content") or []):
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "tool_use":
                    calls.add(part.get("id") or f"{index}:{n}")
                if part.get("type") == "tool_result" and part.get("is_error"):
                    errors.add(part.get("tool_use_id") or f"{index}:{n}")
            if event.get("type") == "tool_result" and event.get("is_error"):
                errors.add(event.get("tool_use_id") or str(index))
            if event.get("type") == "assistant" and isinstance(message.get("usage"), dict):
                usage = message["usage"]
                if usage.get("total_tokens") is not None or usage.get("input_tokens") or usage.get("output_tokens"):
                    observed[message.get("id") or str(index)] = usage
            if event.get("type") == "result" and isinstance(event.get("usage"), dict):
                result = event["usage"]
        else:
            part = event.get("part") or {}
            if event.get("type") == "tool_use":
                identity = part.get("callID") or str(index)
                calls.add(identity)
                if (part.get("state") or {}).get("status") == "error":
                    errors.add(identity)
            if event.get("type") == "step_finish" and isinstance(part.get("tokens"), dict):
                observed[part.get("id") or str(index)] = part["tokens"]
    contributions = [result] if result is not None else list(observed.values())
    totals, inputs, outputs, caches, reasoning = [], [], [], [], []
    for usage in contributions:
        if runtime == "qwen-code":
            inp, out = count(usage.get("input_tokens")), count(usage.get("output_tokens"))
            cache = count(usage.get("cache_read_input_tokens"))
            total = count(usage.get("total_tokens"))
            reason = None
        else:
            inp, out = count(usage.get("input")), count(usage.get("output"))
            cache_data = usage.get("cache") or {}
            cache = count(cache_data.get("read"))
            write = count(cache_data.get("write"))
            if cache is not None and write is not None:
                cache += write
            else:
                cache = None
            total = count(usage.get("total"))
            reason = count(usage.get("reasoning"))
        inputs.append(inp)
        outputs.append(out)
        caches.append(cache)
        totals.append(total)
        reasoning.append(reason)
    def summed(values):
        return sum(values) if values and all(v is not None for v in values) else None
    total, cache = summed(totals), summed(caches)
    complete = (result is not None if runtime == "qwen-code" else exit_status == 0)
    coverage = "complete" if complete and total is not None and not malformed and not restarted else "partial" if contributions else "unknown"
    return {
        "schema_version": 1, "runtime": runtime, "coverage": coverage,
        **({"coverage_reason": "restart: totals cover final attempt only; prior attempt retained separately"}
           if restarted else {}),
        "input_reported": summed(inputs), "output_reported": summed(outputs),
        "reasoning_reported": summed(reasoning), "cache_read_write": cache,
        "total_including_cache": total,
        "total_excluding_cache": total - cache if total is not None and cache is not None and total >= cache else None,
        "total_provenance": "runtime-reported" if total is not None else "unavailable",
        "reasoning_rule": "reported separately; never added to the runtime total",
        "tool_calls": len(calls), "tool_errors": len(errors), "malformed_events": malformed,
    }


def record(path: Path, run: str, usage: dict[str, Any]) -> None:
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        prior = [e for e in records(path) if e.get("run_id") == run]
        value = {"run_id": run, **usage}
        if prior:
            if prior != [value]:
                raise ValueError("Conflicting accounting replay; preserve evidence and adjudicate explicitly")
            return
        append(path, value)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--log", type=Path, required=True)
    p.add_argument("--runtime", choices=["qwen-code", "opencode"], required=True)
    p.add_argument("--exit-status", type=int)
    p.add_argument("--records", type=Path)
    p.add_argument("--run-id")
    args = p.parse_args(argv)
    value = normalize(args.log, args.runtime, args.exit_status)
    if args.records:
        if not args.run_id:
            p.error("--records requires --run-id")
        record(args.records, args.run_id, value)
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
