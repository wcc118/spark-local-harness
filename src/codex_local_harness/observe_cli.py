from __future__ import annotations

import argparse
import json
from pathlib import Path

from .diagnostics import append_record
from .frontier import FrontierStatus
from .workflow import WorkflowEvent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Append bounded workflow or Codex observability facts")
    commands = parser.add_subparsers(dest="kind", required=True)
    workflow = commands.add_parser("workflow")
    workflow.add_argument("--records", type=Path, required=True)
    workflow.add_argument("--event-id", required=True)
    workflow.add_argument("--operation-id", required=True)
    workflow.add_argument("--stage", required=True)
    workflow.add_argument("--state", required=True)
    workflow.add_argument("--observed-at", required=True)
    workflow.add_argument("--issue", type=int, required=True)
    workflow.add_argument("--pull-request", type=int)
    workflow.add_argument("--run-id")
    workflow.add_argument("--role")
    workflow.add_argument("--model")
    workflow.add_argument("--correction-cycle", type=int, default=0)
    workflow.add_argument("--stop-reason")
    workflow.add_argument("--evidence-ref", action="append", default=[])

    frontier = commands.add_parser("frontier")
    frontier.add_argument("--records", type=Path, required=True)
    frontier.add_argument("--status-id", required=True)
    frontier.add_argument("--observed-at", required=True)
    frontier.add_argument("--source", choices=("measured", "operator-reported", "unavailable"), required=True)
    frontier.add_argument("--context-used", type=int)
    frontier.add_argument("--context-window", type=int)
    frontier.add_argument("--plan-remaining-percent", type=float)
    frontier.add_argument("--plan-refresh-at")
    frontier.add_argument("--note-code")

    args = parser.parse_args(argv)
    try:
        if args.kind == "workflow":
            record = WorkflowEvent(
                args.event_id, args.operation_id, args.stage, args.state, args.observed_at, args.issue,
                args.pull_request, args.run_id, args.role, args.model, args.correction_cycle,
                args.stop_reason, tuple(args.evidence_ref),
            )
        else:
            record = FrontierStatus(
                args.status_id, args.observed_at, args.source, args.context_used, args.context_window,
                args.plan_remaining_percent, args.plan_refresh_at, args.note_code,
            )
        append_record(args.records, record)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(record.as_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
