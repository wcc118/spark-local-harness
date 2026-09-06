from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .context_checkpoint import (
    ContextCheckpointEvent,
    append_checkpoint,
    latest_checkpoint,
    read_checkpoints,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Append and read context checkpoint records"
    )
    subparsers = parser.add_subparsers(dest="kind", required=True)

    # append: append a checkpoint record
    append_parser = subparsers.add_parser("append", help="Append a checkpoint record")
    append_parser.add_argument("--records", type=Path, required=True)
    append_parser.add_argument("--checkpoint-id", required=True)
    append_parser.add_argument("--observed-at", required=True)
    append_parser.add_argument("--repository", required=True)
    append_parser.add_argument("--issue", type=int, required=True)
    append_parser.add_argument("--operation-id", required=True)
    append_parser.add_argument("--session-id", required=True)
    append_parser.add_argument("--turn-id", required=True)
    append_parser.add_argument("--plan-event-id", required=True)
    append_parser.add_argument("--plan-revision", type=int, required=True)
    append_parser.add_argument("--objective", required=True)
    append_parser.add_argument("--acceptance-criteria-ref", action="append", required=True)
    append_parser.add_argument("--decision-ref", action="append", required=True)
    append_parser.add_argument("--test-evidence-ref", action="append", required=True)
    append_parser.add_argument("--relevant-file", action="append", required=True)
    append_parser.add_argument("--next-action", required=True)
    append_parser.add_argument("--head-sha", required=True)
    append_parser.add_argument("--pull-request", type=int, default=None)
    append_parser.add_argument("--blocker-ref", action="append", default=[])
    append_parser.add_argument("--evidence-ref", action="append", default=[])

    # read: read all checkpoint records
    read_parser = subparsers.add_parser("read", help="Read all checkpoint records")
    read_parser.add_argument("--records", type=Path, required=True)

    # latest: read latest checkpoint for a session
    latest_parser = subparsers.add_parser("latest", help="Read latest checkpoint for a session")
    latest_parser.add_argument("--records", type=Path, required=True)
    latest_parser.add_argument("--session-id", required=True)

    args = parser.parse_args(argv)
    try:
        if args.kind == "append":
            record = ContextCheckpointEvent(
                checkpoint_id=args.checkpoint_id,
                observed_at=args.observed_at,
                repository=args.repository,
                issue=args.issue,
                operation_id=args.operation_id,
                session_id=args.session_id,
                turn_id=args.turn_id,
                plan_event_id=args.plan_event_id,
                plan_revision=args.plan_revision,
                objective=args.objective,
                acceptance_criteria_refs=tuple(args.acceptance_criteria_ref),
                decision_refs=tuple(args.decision_ref),
                test_evidence_refs=tuple(args.test_evidence_ref),
                relevant_files=tuple(args.relevant_file),
                next_action=args.next_action,
                head_sha=args.head_sha,
                pull_request=args.pull_request,
                blocker_refs=tuple(args.blocker_ref),
                evidence_refs=tuple(args.evidence_ref),
            )
            success = append_checkpoint(args.records, record)
            print(json.dumps({"success": success, "checkpoint_id": record.checkpoint_id}))
        elif args.kind == "read":
            checkpoints = read_checkpoints(args.records)
            result = [cp.as_dict() for cp in checkpoints]
            print(json.dumps(result, indent=2))
        elif args.kind == "latest":
            checkpoints = read_checkpoints(args.records)
            latest = latest_checkpoint(checkpoints, session_id=args.session_id)
            if latest is None:
                print(json.dumps({"status": "not-found", "session_id": args.session_id}))
            else:
                print(json.dumps({"status": "found", "checkpoint": latest.as_dict()}))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
