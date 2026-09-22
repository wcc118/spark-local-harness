"""Phase 1 vision-gate judge (condition B): score frozen fixtures with the 3090's Qwen3.8-27B.

Sends each frozen fixture's screenshot to the LM Studio lane, three times per
fixture (repeats), and records the raw and parsed response. Never repairs an
invalid response silently: an unparsable or schema-invalid reply is retried
once (an infrastructure allowance, not a content fix) and, if it still fails,
recorded as schema_invalid. Requires the lane to be healthy before starting
and holds the 3090 lease for the whole run; refuses to run "as if" the lane
were unavailable, unlike the fail-open production lane guard, because a
silent skip here would look like an untested fixture, not an honest result.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent.parent / "src"))

from codex_local_harness.lane import GpuLease, check_lane  # noqa: E402

PROMPT = """You are reviewing a screenshot of a dashboard or data panel for defects.

Look for things that are actually wrong or inconsistent WITHIN this single image: numbers that
contradict each other, a chart that contradicts its own legend or axis, a value that does not
match the table it is next to, misaligned or overlapping content, placeholder or template text
left unresolved, implausible dates, or anything else a careful reviewer would flag. Only report
something you can point to concretely in this image; do not guess about data you cannot see. If
the page looks internally consistent and correct, report an empty findings list.

Separately, transcribe every clearly legible labelled numeric value you can read in the image
(for example "Damping ratio: 0.037500"), exactly as printed, including sign, units and precision.
If you are not confident of a digit, omit that field rather than guessing.

Reply with EXACTLY ONE JSON object and nothing else (no prose, no markdown fence), matching this
shape:
{"findings": [{"description": "...", "region": "...", "severity": "low|medium|high"}],
 "numeric_readings": [{"label": "...", "value": "..."}]}
"""

REQUIRED_TOP_KEYS = {"findings", "numeric_readings"}
FINDING_KEYS = {"description", "region", "severity"}
SEVERITIES = {"low", "medium", "high"}
READING_KEYS = {"label", "value"}
FENCE_RE = re.compile(r"^```(?:json)?\s*\n(.*)\n```\s*$", re.DOTALL)


def unwrap_fence(text: str) -> str:
    m = FENCE_RE.match(text.strip())
    return m.group(1) if m else text


def validate_schema(obj: Any) -> str | None:
    """Return None if valid, else a short reason."""
    if not isinstance(obj, dict):
        return "top level is not an object"
    if set(obj.keys()) < REQUIRED_TOP_KEYS:
        return f"missing required key(s): {REQUIRED_TOP_KEYS - set(obj.keys())}"
    findings = obj["findings"]
    readings = obj["numeric_readings"]
    if not isinstance(findings, list):
        return "'findings' is not a list"
    if not isinstance(readings, list):
        return "'numeric_readings' is not a list"
    for i, f in enumerate(findings):
        if not isinstance(f, dict) or not FINDING_KEYS <= set(f.keys()):
            return f"findings[{i}] missing required keys"
        if not all(isinstance(f[k], str) for k in FINDING_KEYS):
            return f"findings[{i}] has a non-string field"
        if f["severity"] not in SEVERITIES:
            return f"findings[{i}].severity is not one of {SEVERITIES}"
    for i, r in enumerate(readings):
        if not isinstance(r, dict) or not READING_KEYS <= set(r.keys()):
            return f"numeric_readings[{i}] missing required keys"
        if not all(isinstance(r[k], str) for k in READING_KEYS):
            return f"numeric_readings[{i}] has a non-string field"
    return None


def call_model(base_url: str, model: str, png_bytes: bytes, timeout: float, max_tokens: int) -> dict[str, Any]:
    b64 = base64.b64encode(png_bytes).decode("ascii")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }
        ],
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    elapsed = time.monotonic() - started
    message = raw["choices"][0]["message"]
    content = message.get("content") or ""
    usage = raw.get("usage") or {}
    return {"raw_response": raw, "content": content, "elapsed_seconds": elapsed, "usage": usage}


def score_one_call(base_url: str, model: str, png_bytes: bytes, timeout: float, max_tokens: int) -> dict[str, Any]:
    attempts = []
    for attempt in range(2):  # one retry allowance for an unparsable/invalid reply
        try:
            result = call_model(base_url, model, png_bytes, timeout, max_tokens)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            attempts.append({"attempt": attempt, "outcome": "request-error", "error": str(exc)})
            continue
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            attempts.append({"attempt": attempt, "outcome": "malformed-http-response", "error": str(exc)})
            continue
        text = unwrap_fence(result["content"])
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            attempts.append({
                "attempt": attempt, "outcome": "invalid-json", "error": str(exc),
                "content": result["content"], "elapsed_seconds": result["elapsed_seconds"], "usage": result["usage"],
            })
            continue
        reason = validate_schema(parsed)
        if reason is not None:
            attempts.append({
                "attempt": attempt, "outcome": "schema-invalid", "reason": reason,
                "content": result["content"], "elapsed_seconds": result["elapsed_seconds"], "usage": result["usage"],
            })
            continue
        attempts.append({
            "attempt": attempt, "outcome": "ok", "parsed": parsed, "content": result["content"],
            "elapsed_seconds": result["elapsed_seconds"], "usage": result["usage"],
        })
        return {"outcome": "ok", "parsed": parsed, "attempts": attempts}
    final_outcome = attempts[-1]["outcome"] if attempts else "no-attempts"
    return {"outcome": final_outcome, "parsed": None, "attempts": attempts}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=None, help="Defaults to $LMSTUDIO_BASE_URL")
    parser.add_argument("--model", default="qwen/qwen3.8-27b")
    parser.add_argument("--min-context", type=int, default=65536)
    parser.add_argument("--fixtures-dir", type=Path, default=ROOT / "fixtures" / "frozen")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--only", action="append", default=None, help="Restrict to these fixture ids")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--max-tokens", type=int, default=3200)
    parser.add_argument("--lease-timeout", type=float, default=60.0)
    args = parser.parse_args()

    import os
    base_url = args.base_url or os.environ.get("LMSTUDIO_BASE_URL")
    if not base_url:
        print("ERROR: --base-url or LMSTUDIO_BASE_URL is required", file=sys.stderr)
        return 2

    status = check_lane(base_url, args.model, min_context=args.min_context, timeout=10.0)
    if status.status != "ok":
        print(f"ERROR: lane not ok: {json.dumps(status.as_dict())}", file=sys.stderr)
        return 10

    fixture_dirs = sorted(p for p in args.fixtures_dir.iterdir() if p.is_dir())
    if args.only:
        fixture_dirs = [p for p in fixture_dirs if p.name in set(args.only)]
    if args.limit:
        fixture_dirs = fixture_dirs[: args.limit]
    if not fixture_dirs:
        print("ERROR: no fixtures found", file=sys.stderr)
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    status_path = args.out / "status.json"

    lease = GpuLease("3090", purpose="vision-gate-judge")
    if not lease.acquire(timeout=args.lease_timeout):
        print(f"ERROR: could not acquire the 3090 lease within {args.lease_timeout}s", file=sys.stderr)
        return 75

    counts = {"ok": 0, "invalid-json": 0, "schema-invalid": 0, "request-error": 0, "malformed-http-response": 0}
    try:
        for fixture_dir in fixture_dirs:
            png_candidates = list(fixture_dir.glob("*/1280x800.png")) or list(fixture_dir.glob("1280x800.png"))
            if not png_candidates:
                print(f"ERROR: no PNG found for fixture {fixture_dir.name}", file=sys.stderr)
                return 2
            png_bytes = png_candidates[0].read_bytes()
            fixture_out = args.out / fixture_dir.name
            fixture_out.mkdir(parents=True, exist_ok=True)
            for repeat in range(args.repeats):
                result = score_one_call(base_url, args.model, png_bytes, args.timeout, args.max_tokens)
                counts[result["outcome"]] = counts.get(result["outcome"], 0) + 1
                record = {
                    "fixture_id": fixture_dir.name,
                    "repeat": repeat,
                    "model": args.model,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                    **result,
                }
                (fixture_out / f"repeat-{repeat}.json").write_text(
                    json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
                print(json.dumps({"fixture": fixture_dir.name, "repeat": repeat, "outcome": result["outcome"]}))
                status_path.write_text(json.dumps({"counts": counts, "last_fixture": fixture_dir.name, "last_repeat": repeat}, sort_keys=True) + "\n")
    finally:
        lease.release()

    (args.out / "run_summary.json").write_text(
        json.dumps({
            "base_url": base_url, "model": args.model, "repeats": args.repeats,
            "fixtures": [p.name for p in fixture_dirs], "counts": counts,
            "lane_status_at_start": status.as_dict(),
        }, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps({"summary": counts}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
