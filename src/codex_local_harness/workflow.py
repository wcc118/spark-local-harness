from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .diagnostics import DIAGNOSTIC_SCHEMA_VERSION, _safe_atom, _timestamp


# Version-one stage names remain valid forever because the source ledger is append-only.
STAGES = (
    "intake", "routing", "local-work", "delivery", "review", "evidence",
    "codex-decision", "correction", "merge", "post-merge",
)
STATES = {
    "pending", "active", "passed", "failed", "interrupted", "blocked",
    "awaiting-adjudication", "superseded", "not-applicable",
}
MILESTONES = (
    "intake", "routing-decision", "implementation", "delivery", "review",
    "codex-decision", "merge", "post-merge",
)
MILESTONE_LABELS = {
    "intake": "intake",
    "routing-decision": "routing / decision",
    "implementation": "implementation",
    "delivery": "delivery",
    "review": "review",
    "codex-decision": "Codex decision",
    "merge": "merge",
    "post-merge": "post-merge",
}
STAGE_TO_MILESTONE = {
    "intake": "intake",
    "routing": "routing-decision",
    "local-work": "implementation",
    "delivery": "delivery",
    "review": "review",
    "codex-decision": "codex-decision",
    "merge": "merge",
    "post-merge": "post-merge",
}
ACTIVITY_STAGES = {"evidence", "correction"}
TERMINAL_STATES = {"passed", "failed", "interrupted", "blocked", "superseded", "not-applicable"}
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\/[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _validate_repository(value: str | None, name: str) -> None:
    if value is not None and value.strip() != "" and not REPOSITORY_PATTERN.fullmatch(value):
        raise ValueError(f"{name} must be a valid OWNER/REPO identifier")


@dataclass(frozen=True)
class WorkflowEvent:
    """Immutable v1 workflow fact.

    The v0.3 read model projects these historical facts into milestones and
    activities. Keeping this wire contract stable makes every prior ledger
    replayable and provides a tested rollback path to the v0.2 dashboard.
    """

    event_id: str
    operation_id: str
    stage: str
    state: str
    observed_at: str
    issue: int
    pull_request: int | None = None
    run_id: str | None = None
    role: str | None = None
    model: str | None = None
    correction_cycle: int = 0
    stop_reason: str | None = None
    evidence_refs: tuple[str, ...] = ()
    session_id: str | None = None
    repository: str | None = None
    schema_version: int = DIAGNOSTIC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _safe_atom(self.event_id, "event_id")
        _safe_atom(self.operation_id, "operation_id")
        _timestamp(self.observed_at)
        if self.stage not in STAGES:
            raise ValueError("invalid workflow stage")
        if self.state not in STATES:
            raise ValueError("invalid workflow state")
        if self.issue <= 0 or (self.pull_request is not None and self.pull_request <= 0):
            raise ValueError("issue and pull request must be positive")
        if self.correction_cycle < 0:
            raise ValueError("correction_cycle must be non-negative")
        if self.session_id is not None:
            _safe_atom(self.session_id, "session_id")
        _validate_repository(self.repository, "repository")
        for name, value in (("run_id", self.run_id), ("role", self.role), ("model", self.model), ("stop_reason", self.stop_reason)):
            if value is not None:
                _safe_atom(value, name)
        for reference in self.evidence_refs:
            _safe_atom(reference, "evidence_ref")
        if self.schema_version != DIAGNOSTIC_SCHEMA_VERSION:
            raise ValueError("unsupported workflow schema version")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> WorkflowEvent:
        data = dict(payload)
        data["evidence_refs"] = tuple(data.get("evidence_refs", ()))
        return cls(**data)


def _event_identity(event: WorkflowEvent) -> tuple[Any, ...]:
    return (event.session_id, event.repository, event.operation_id)


def _event_payload(event: WorkflowEvent) -> str:
    return json.dumps(event.as_dict(), sort_keys=True, separators=(",", ":"))


def _activity_milestone(event: WorkflowEvent, milestone_events: list[WorkflowEvent]) -> str:
    if event.stage == "correction":
        return "implementation"
    earlier = [item for item in milestone_events if (item.observed_at, item.event_id) <= (event.observed_at, event.event_id)]
    if earlier:
        return STAGE_TO_MILESTONE[earlier[-1].stage]
    return "unassigned"


def _operation_projection(items: list[WorkflowEvent]) -> dict[str, Any]:
    file_order = list(items)
    ordered = sorted(items, key=lambda event: (event.observed_at, event.event_id))
    payloads_by_id: dict[str, set[str]] = {}
    for event in ordered:
        payloads_by_id.setdefault(event.event_id, set()).add(_event_payload(event))
    conflicts = sorted(event_id for event_id, payloads in payloads_by_id.items() if len(payloads) > 1)

    canonical: dict[str, WorkflowEvent] = {}
    for event in ordered:
        canonical[event.event_id] = event
    events = sorted(canonical.values(), key=lambda event: (event.observed_at, event.event_id))
    latest_by_stage: dict[str, WorkflowEvent] = {}
    for event in events:
        latest_by_stage[event.stage] = event
    stages = [
        {
            "stage": stage,
            "state": latest_by_stage[stage].state if stage in latest_by_stage else "pending",
            "observed_at": latest_by_stage[stage].observed_at if stage in latest_by_stage else None,
            "evidence_refs": list(latest_by_stage[stage].evidence_refs) if stage in latest_by_stage else [],
        }
        for stage in STAGES
    ]
    milestone_events = [event for event in events if event.stage in STAGE_TO_MILESTONE]
    latest_by_milestone: dict[str, WorkflowEvent] = {}
    for event in milestone_events:
        latest_by_milestone[STAGE_TO_MILESTONE[event.stage]] = event

    activities = []
    for event in events:
        if event.stage not in ACTIVITY_STAGES:
            continue
        activities.append({
            "activity_type": event.stage,
            "milestone": _activity_milestone(event, milestone_events),
            "state": event.state,
            "observed_at": event.observed_at,
            "run_id": event.run_id,
            "role": event.role,
            "correction_cycle": event.correction_cycle,
            "stop_reason": event.stop_reason,
            "evidence_refs": list(event.evidence_refs),
        })

    milestones = []
    for name in MILESTONES:
        event = latest_by_milestone.get(name)
        milestones.append({
            "milestone": name,
            "label": MILESTONE_LABELS[name],
            "state": event.state if event else "pending",
            "observed_at": event.observed_at if event else None,
            "evidence_refs": list(event.evidence_refs) if event else [],
            "activities": [activity for activity in activities if activity["milestone"] == name],
        })

    newest = events[-1]
    current_milestone = STAGE_TO_MILESTONE.get(newest.stage, _activity_milestone(newest, milestone_events))
    diagnostics = []
    if conflicts:
        diagnostics.append({"kind": "conflicting-event-reuse", "event_ids": conflicts})
    if newest.repository is not None and newest.session_id is None:
        diagnostics.append({"kind": "legacy-session-identity", "state": "unknown"})
    if any(current.observed_at < previous.observed_at for previous, current in zip(file_order, file_order[1:])):
        diagnostics.append({"kind": "stale-event-order", "state": "stale"})
    github_observations = [
        event for event in events
        if any(reference.startswith("github-label:") for reference in event.evidence_refs)
    ]
    latest_github = github_observations[-1] if github_observations else None
    github_labels = {
        reference.removeprefix("github-label:")
        for reference in (latest_github.evidence_refs if latest_github else ())
        if reference.startswith("github-label:")
    }
    if github_labels:
        expected = {
            "implementation": {"status/in-progress", "status/review"},
            "review": {"status/review", "status/review-passed", "status/blocked"},
            "codex-decision": {"status/review", "status/review-passed", "status/blocked"},
            "merge": {"status/review-passed", "status/done"},
            "post-merge": {"status/done"},
        }.get(current_milestone, {"status/backlog", "status/in-progress"})
        if not github_labels & expected:
            diagnostics.append({
                "kind": "github-label-divergence", "state": "divergent",
                "observed": sorted(github_labels), "expected": sorted(expected),
            })
    else:
        diagnostics.append({"kind": "github-label-unavailable", "state": "unknown"})

    return {
        "operation_id": newest.operation_id,
        "issue": newest.issue,
        "pull_request": newest.pull_request,
        "run_id": newest.run_id,
        "run_ids": sorted({event.run_id for event in events if event.run_id}),
        "role": newest.role,
        "model": newest.model,
        "correction_cycle": max((event.correction_cycle for event in events), default=0),
        "current_stage": newest.stage,
        "current_milestone": current_milestone,
        "current_state": "conflicting" if conflicts else newest.state,
        "stop_reason": newest.stop_reason,
        "updated_at": newest.observed_at,
        "stages": stages,
        "milestones": milestones,
        "activities": activities,
        "diagnostics": diagnostics,
        "session_id": newest.session_id,
        "repository": newest.repository,
        "projection_version": 2,
    }


def derive_operations(events: Iterable[WorkflowEvent]) -> tuple[dict[str, Any], ...]:
    grouped: dict[tuple[str | None, str | None, str], list[WorkflowEvent]] = {}
    for event in events:
        grouped.setdefault(_event_identity(event), []).append(event)
    operations = [_operation_projection(items) for items in grouped.values()]
    return tuple(sorted(operations, key=lambda item: (item["updated_at"], item["operation_id"]), reverse=True))


def validate_append(existing: Iterable[WorkflowEvent], event: WorkflowEvent) -> None:
    """Validate identity and transition facts before a publisher appends.

    Historical readers remain permissive so malformed/conflicting facts stay
    visible. New publication is strict and rejects event-id reuse, identity
    drift, stage regression, and invalid correction cycles.
    """

    if event.stage == "correction" and event.correction_cycle <= 0 and event.state != "not-applicable":
        raise ValueError("correction events require a positive correction_cycle")
    related = [item for item in existing if _event_identity(item) == _event_identity(event)]
    for item in existing:
        if item.event_id == event.event_id:
            if _event_payload(item) == _event_payload(event):
                raise ValueError("workflow event already exists")
            raise ValueError("conflicting workflow event_id reuse")
    if not related:
        if event.stage not in {"intake", "routing", "local-work", "evidence"}:
            raise ValueError("new operation must begin with intake, routing, implementation, or bounded evidence")
        return
    newest = max(related, key=lambda item: (item.observed_at, item.event_id))
    if event.observed_at < newest.observed_at:
        raise ValueError("workflow event is stale")
    if (event.issue, event.repository, event.session_id) != (newest.issue, newest.repository, newest.session_id):
        raise ValueError("workflow identity changed within operation")
    if event.stage == "correction":
        prior_cycle = max((item.correction_cycle for item in related), default=0)
        if event.correction_cycle not in {prior_cycle, prior_cycle + 1}:
            raise ValueError("correction cycle must continue monotonically")
        return
    if event.stage in ACTIVITY_STAGES:
        return
    previous = STAGE_TO_MILESTONE.get(newest.stage)
    current = STAGE_TO_MILESTONE[event.stage]
    if previous and MILESTONES.index(current) < MILESTONES.index(previous):
        latest_correction = max((item.correction_cycle for item in related if item.stage == "correction"), default=0)
        if current != "implementation" or latest_correction <= 0:
            raise ValueError("illegal milestone regression")


def read_workflow_records(path: Path) -> tuple[WorkflowEvent, ...]:
    if not path.exists():
        return ()
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(WorkflowEvent.from_dict(json.loads(line)))
    return tuple(records)
