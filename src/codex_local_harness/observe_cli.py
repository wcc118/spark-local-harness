from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .diagnostics import append_record
from .frontier import FrontierStatus
from .workflow import STAGE_TO_MILESTONE, WorkflowEvent, derive_operations, read_workflow_records, validate_append


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
    workflow.add_argument("--session-id")
    workflow.add_argument("--repository")
    workflow.add_argument("--actor", choices=("codex", "worker", "operator", "system"))
    workflow.add_argument("--source", choices=("codex-cli", "worker-wrapper", "operator", "replay", "legacy"))
    workflow.add_argument("--milestone")
    workflow.add_argument("--prior-milestone")
    workflow.add_argument("--activity-type")

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
    frontier.add_argument("--tokens-used", type=int)

    args = parser.parse_args(argv)
    try:
        if args.kind == "workflow":
            session_id = (
                args.session_id
                or os.environ.get("CODEX_LOCAL_SESSION_ID")
                or os.environ.get("CODEX_THREAD_ID")
            )
            repository = args.repository or os.environ.get("CODEX_LOCAL_REPOSITORY")
            existing = read_workflow_records(args.records)
            related = [item for item in derive_operations(existing) if (
                item["operation_id"], item["session_id"], item["repository"]
            ) == (args.operation_id, session_id, repository)]
            prior_milestone = args.prior_milestone or (related[0]["current_milestone"] if related else None)
            if args.milestone:
                milestone = args.milestone
            elif args.stage == "correction":
                milestone = "implementation"
            elif args.stage == "evidence":
                milestone = prior_milestone or "unassigned"
            else:
                milestone = STAGE_TO_MILESTONE[args.stage]
            activity_type = args.activity_type or (args.stage if args.stage in {"evidence", "correction"} else None)
            record = WorkflowEvent(
                event_id=args.event_id, operation_id=args.operation_id, stage=args.stage,
                state=args.state, observed_at=args.observed_at, issue=args.issue,
                pull_request=args.pull_request, run_id=args.run_id, role=args.role,
                model=args.model, correction_cycle=args.correction_cycle,
                stop_reason=args.stop_reason, evidence_refs=tuple(args.evidence_ref),
                session_id=session_id, repository=repository,
                actor=args.actor or ("worker" if args.role else "codex"),
                source=args.source or ("worker-wrapper" if args.role else "codex-cli"),
                prior_milestone=prior_milestone if prior_milestone in STAGE_TO_MILESTONE.values() else None,
                milestone=milestone, activity_type=activity_type, schema_version=2,
            )
            validate_append(existing, record)
        else:
            record = FrontierStatus(
                args.status_id, args.observed_at, args.source, args.context_used, args.context_window,
                args.plan_remaining_percent, args.plan_refresh_at, args.note_code,
                args.tokens_used,
            )
        append_record(args.records, record)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(record.as_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
