from __future__ import annotations

import argparse
import json
from pathlib import Path

from .diagnostics import Adjudication, LifecycleEvent, append_record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Append a sanitized harness diagnostic fact")
    subparsers = parser.add_subparsers(dest="kind", required=True)

    adjudicate = subparsers.add_parser("adjudicate")
    adjudicate.add_argument("--records", type=Path, required=True)
    adjudicate.add_argument("--adjudication-id", required=True)
    adjudicate.add_argument("--run-id", required=True)
    adjudicate.add_argument("--verdict", required=True)
    adjudicate.add_argument("--source", required=True)
    adjudicate.add_argument("--observed-at", required=True)
    adjudicate.add_argument("--failure-class")
    adjudicate.add_argument("--correction-of")
    adjudicate.add_argument("--correction-cycle", type=int, default=0)
    adjudicate.add_argument("--intervention-depth", default="none")
    adjudicate.add_argument("--codex-decision")
    adjudicate.add_argument("--evidence-ref", action="append", default=[])

    lifecycle = subparsers.add_parser("lifecycle")
    lifecycle.add_argument("--records", type=Path, required=True)
    lifecycle.add_argument("--event-id", required=True)
    lifecycle.add_argument("--run-id", required=True)
    lifecycle.add_argument("--event", required=True)
    lifecycle.add_argument("--observed-at", required=True)
    lifecycle.add_argument("--wall-seconds", type=float)
    lifecycle.add_argument("--exit-status", type=int)
    lifecycle.add_argument("--dirty-entries", type=int)

    args = parser.parse_args(argv)
    try:
        if args.kind == "adjudicate":
            record = Adjudication(
                adjudication_id=args.adjudication_id,
                run_id=args.run_id,
                verdict=args.verdict,
                source=args.source,
                observed_at=args.observed_at,
                failure_class=args.failure_class,
                correction_of=args.correction_of,
                correction_cycle=args.correction_cycle,
                intervention_depth=args.intervention_depth,
                codex_decision=args.codex_decision,
                evidence_refs=tuple(args.evidence_ref),
            )
        else:
            record = LifecycleEvent(
                event_id=args.event_id,
                run_id=args.run_id,
                event=args.event,
                observed_at=args.observed_at,
                wall_seconds=args.wall_seconds,
                exit_status=args.exit_status,
                dirty_entries=args.dirty_entries,
            )
        append_record(args.records, record)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(record.as_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
