from __future__ import annotations

import argparse
import json
from pathlib import Path

from .diagnostics import PublicationFailure, append_record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Record a sanitized telemetry-publication failure")
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--failure-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--phase", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--error-class", required=True)
    parser.add_argument("--observed-at", required=True)
    args = parser.parse_args(argv)
    try:
        record = PublicationFailure(
            args.failure_id,
            args.run_id,
            args.phase,
            args.target,
            args.error_class,
            args.observed_at,
        )
        append_record(args.records, record)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(record.as_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
