from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .diagnostics import DIAGNOSTIC_SCHEMA_VERSION, _safe_atom, _timestamp


STAGES = ("intake", "routing", "local-work", "delivery", "review", "evidence", "codex-decision", "correction", "merge", "post-merge")
STATES = {
    "pending",
    "active",
    "passed",
    "failed",
    "interrupted",
    "blocked",
    "awaiting-adjudication",
    "superseded",
    "not-applicable",
}

# Strict OWNER/REPO regex for GitHub repositories
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\/[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _validate_repository(value: str | None, name: str) -> None:
    """Validate GitHub repository format: OWNER/REPO"""
    if value is not None and value.strip() != "":
        if not REPOSITORY_PATTERN.fullmatch(value):
            raise ValueError(f"{name} must be a valid OWNER/REPO identifier")


@dataclass(frozen=True)
class WorkflowEvent:
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


def derive_operations(events: Iterable[WorkflowEvent]) -> tuple[dict[str, Any], ...]:
    grouped: dict[tuple[str | None, str | None, str], list[WorkflowEvent]] = {}
    for event in events:
        # Use "legacy" as grouping key when session_id or repository is None
        session_id = event.session_id if event.session_id is not None else "legacy"
        repository = event.repository if event.repository is not None else "legacy"
        key = (session_id, repository, event.operation_id)
        grouped.setdefault(key, []).append(event)
    operations = []
    for (session_id, repository, operation_id), items in grouped.items():
        latest_by_stage: dict[str, WorkflowEvent] = {}
        for event in items:
            prior = latest_by_stage.get(event.stage)
            if prior is None or (event.observed_at, event.event_id) > (prior.observed_at, prior.event_id):
                latest_by_stage[event.stage] = event
        newest = max(items, key=lambda event: (event.observed_at, event.event_id))
        stages = []
        for stage in STAGES:
            event = latest_by_stage.get(stage)
            stages.append({"stage": stage, "state": event.state if event else "pending", "observed_at": event.observed_at if event else None, "evidence_refs": list(event.evidence_refs) if event else []})
        operations.append({
            "operation_id": operation_id,
            "issue": newest.issue,
            "pull_request": newest.pull_request,
            "run_id": newest.run_id,
            "run_ids": sorted({event.run_id for event in items if event.run_id}),
            "role": newest.role,
            "model": newest.model,
            "correction_cycle": newest.correction_cycle,
            "current_stage": newest.stage,
            "current_state": newest.state,
            "stop_reason": newest.stop_reason,
            "updated_at": newest.observed_at,
            "stages": stages,
            "session_id": session_id if session_id != "legacy" else None,
            "repository": repository if repository != "legacy" else None,
        })
    return tuple(sorted(operations, key=lambda item: (item["updated_at"], item["operation_id"]), reverse=True))
