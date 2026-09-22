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
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent.parent / "src"))

from codex_local_harness.lane import GpuLease, check_lane  # noqa: E402
from codex_local_harness.vision_judge import (  # noqa: E402
    PROMPT,
    call_model,
    score_one_call,
    unwrap_fence,
    validate_schema,
)


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
