from __future__ import annotations

import argparse
import json
from pathlib import Path

from .diagnostics import append_record
from .plan_event import PlanEvent, read_plan_records, validate_append


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish concise public harness plan state")
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--event-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.event_file.read_text(encoding="utf-8"))
        record = PlanEvent.from_dict(payload)
        validate_append(read_plan_records(args.records), record)
        append_record(args.records, record)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(record.as_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
