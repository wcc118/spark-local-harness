from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .context_controller import (
    CompactionLifecycleEvent,
    append_compaction_record,
    read_compaction_records,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Append and read compaction lifecycle records"
    )
    subparsers = parser.add_subparsers(dest="kind", required=True)

    # append: append a compaction record
    append_parser = subparsers.add_parser("append", help="Append a compaction lifecycle record")
    append_parser.add_argument("--records", type=Path, required=True)
    append_parser.add_argument("--record-id", required=True)
    append_parser.add_argument("--compaction-id", required=True)
    append_parser.add_argument("--observed-at", required=True)
    append_parser.add_argument("--state", required=True)
    append_parser.add_argument("--trigger", required=True)
    append_parser.add_argument("--surface", required=True)
    append_parser.add_argument("--run-id", required=True)
    append_parser.add_argument("--session-id", required=True)
    append_parser.add_argument("--repository", required=True)
    append_parser.add_argument("--issue", type=int, required=True)
    append_parser.add_argument("--turn-id", default=None)
    append_parser.add_argument("--item-id", default=None)
    append_parser.add_argument("--before-input-tokens", type=int, default=None)
    append_parser.add_argument("--after-input-tokens", type=int, default=None)
    append_parser.add_argument("--context-window", type=int, default=None)
    append_parser.add_argument("--error-class", default=None)

    # read: read all compaction records
    read_parser = subparsers.add_parser("read", help="Read all compaction records")
    read_parser.add_argument("--records", type=Path, required=True)

    # summary: read compaction count and outcomes
    summary_parser = subparsers.add_parser("summary", help="Read compaction summary")
    summary_parser.add_argument("--records", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        if args.kind == "append":
            record = CompactionLifecycleEvent(
                record_id=args.record_id,
                compaction_id=args.compaction_id,
                observed_at=args.observed_at,
                state=args.state,
                trigger=args.trigger,
                surface=args.surface,
                run_id=args.run_id,
                session_id=args.session_id,
                repository=args.repository,
                issue=args.issue,
                turn_id=args.turn_id,
                item_id=args.item_id,
                before_input_tokens=args.before_input_tokens,
                after_input_tokens=args.after_input_tokens,
                context_window=args.context_window,
                error_class=args.error_class,
            )
            success = append_compaction_record(args.records, record)
            print(json.dumps({"success": success, "record_id": record.record_id}))
        elif args.kind == "read":
            records = read_compaction_records(args.records)
            result = [r.as_dict() for r in records]
            print(json.dumps(result, indent=2))
        elif args.kind == "summary":
            records = read_compaction_records(args.records)
            states: dict[str, int] = {}
            for r in records:
                states[r.state] = states.get(r.state, 0) + 1
            result = {
                "total": len(records),
                "by_state": states,
                "automatic_count": sum(1 for r in records if r.trigger == "automatic"),
                "manual_count": sum(1 for r in records if r.trigger == "manual"),
                "primary_codex_count": sum(1 for r in records if r.surface == "primary-codex"),
                "local_opencode_count": sum(1 for r in records if r.surface == "local-opencode"),
            }
            print(json.dumps(result, indent=2))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
