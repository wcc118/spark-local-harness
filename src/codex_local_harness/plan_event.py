from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .diagnostics import _required, _safe_atom, _timestamp
from .workflow import REPOSITORY_PATTERN


PLAN_SCHEMA_VERSION = 1
STEP_STATES = {"completed", "active", "pending", "blocked", "skipped"}
PLAN_SOURCES = {"codex", "operator", "replay"}
MAX_STEPS = 24
MAX_PUBLIC_TEXT = 500


def _public_text(value: str, name: str) -> None:
    _required(value, name)
    if len(value) > MAX_PUBLIC_TEXT or any(ord(char) < 32 and char not in "\t" for char in value):
        raise ValueError(f"{name} must be concise printable text")


def _identity(event: PlanEvent) -> tuple[str | None, str | None, str, int]:
    return (event.session_id, event.repository, event.operation_id, event.issue)


@dataclass(frozen=True)
class PlanEvent:
    """A complete public plan revision, never private reasoning."""

    event_id: str
    operation_id: str
    issue: int
    objective: str
    steps: tuple[dict[str, Any], ...]
    observed_at: str
    source: str
    revision: int = 1
    session_id: str | None = None
    repository: str | None = None
    parent_event_id: str | None = None
    revision_reason: str | None = None
    evidence_refs: tuple[str, ...] = ()
    schema_version: int = PLAN_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _safe_atom(self.event_id, "event_id")
        _safe_atom(self.operation_id, "operation_id")
        _timestamp(self.observed_at)
        _public_text(self.objective, "objective")
        if self.issue <= 0 or self.revision <= 0:
            raise ValueError("issue and revision must be positive")
        if self.source not in PLAN_SOURCES:
            raise ValueError("invalid plan source")
        if self.session_id is not None:
            _safe_atom(self.session_id, "session_id")
        if self.repository is not None and not REPOSITORY_PATTERN.fullmatch(self.repository):
            raise ValueError("repository must be a valid OWNER/REPO identifier")
        if self.parent_event_id is not None:
            _safe_atom(self.parent_event_id, "parent_event_id")
        if self.revision_reason is not None:
            _public_text(self.revision_reason, "revision_reason")
        if not 1 <= len(self.steps) <= MAX_STEPS:
            raise ValueError("plan must contain 1 to 24 steps")
        step_ids = []
        active = 0
        for step in self.steps:
            if set(step) - {"step_id", "summary", "state", "evidence_refs"}:
                raise ValueError("unknown plan step field")
            step_id = step.get("step_id")
            summary = step.get("summary")
            state = step.get("state")
            if not isinstance(step_id, str) or not isinstance(summary, str):
                raise ValueError("plan step requires step_id and summary strings")
            _safe_atom(step_id, "step_id")
            _public_text(summary, "step summary")
            if state not in STEP_STATES:
                raise ValueError("invalid plan step state")
            active += state == "active"
            references = step.get("evidence_refs", [])
            if not isinstance(references, list) or not all(isinstance(item, str) for item in references):
                raise ValueError("step evidence_refs must be a string list")
            for reference in references:
                _safe_atom(reference, "step evidence_ref")
            step_ids.append(step_id)
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("plan step_id values must be unique")
        if active > 1:
            raise ValueError("plan may have at most one active step")
        for reference in self.evidence_refs:
            _safe_atom(reference, "evidence_ref")
        if self.schema_version != PLAN_SCHEMA_VERSION:
            raise ValueError("unsupported plan schema version")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PlanEvent:
        data = dict(payload)
        data["steps"] = tuple(data.get("steps", ()))
        data["evidence_refs"] = tuple(data.get("evidence_refs", ()))
        return cls(**data)


def validate_append(existing: Iterable[PlanEvent], event: PlanEvent) -> None:
    records = list(existing)
    for item in records:
        if item.event_id == event.event_id:
            if item.as_dict() == event.as_dict():
                raise ValueError("plan event already exists")
            raise ValueError("conflicting plan event_id reuse")
    related = [item for item in records if _identity(item) == _identity(event)]
    if not related:
        if event.revision != 1 or event.parent_event_id is not None:
            raise ValueError("new plan must begin at revision 1 without a parent")
        return
    newest = max(related, key=lambda item: (item.revision, item.observed_at, item.event_id))
    if event.observed_at < newest.observed_at:
        raise ValueError("plan event is stale")
    if event.revision != newest.revision + 1:
        raise ValueError("plan revision must advance exactly once")
    if event.parent_event_id != newest.event_id:
        raise ValueError("plan revision must reference the canonical parent")


def derive_plans(events: Iterable[PlanEvent], *, now: datetime | None = None, stale_after_seconds: int = 1800) -> tuple[dict[str, Any], ...]:
    grouped: dict[tuple[str | None, str | None, str, int], list[PlanEvent]] = {}
    for event in events:
        grouped.setdefault(_identity(event), []).append(event)
    plans = []
    for identity, items in grouped.items():
        file_order = list(items)
        ordered = sorted(items, key=lambda item: (item.revision, item.observed_at, item.event_id))
        by_id: dict[str, set[str]] = {}
        for item in ordered:
            by_id.setdefault(item.event_id, set()).add(json.dumps(item.as_dict(), sort_keys=True))
        conflicts = sorted(key for key, payloads in by_id.items() if len(payloads) > 1)
        revision_counts: dict[int, int] = {}
        for item in ordered:
            revision_counts[item.revision] = revision_counts.get(item.revision, 0) + 1
        revision_conflicts = sorted(key for key, count in revision_counts.items() if count > 1)
        canonical = max(ordered, key=lambda item: (item.revision, item.observed_at, item.event_id))
        history = [
            {
                "event_id": item.event_id, "revision": item.revision,
                "observed_at": item.observed_at, "source": item.source,
                "revision_reason": item.revision_reason,
                "state": "current" if item is canonical else "superseded",
            }
            for item in ordered
        ]
        diagnostics = []
        if conflicts:
            diagnostics.append({"kind": "conflicting-event-reuse", "event_ids": conflicts})
        if revision_conflicts:
            diagnostics.append({"kind": "conflicting-revision", "revisions": revision_conflicts})
        if any(current.observed_at < previous.observed_at for previous, current in zip(file_order, file_order[1:])):
            diagnostics.append({"kind": "stale-event-order", "state": "stale"})
        expected = set(range(1, canonical.revision + 1))
        missing = sorted(expected - set(revision_counts))
        if missing:
            diagnostics.append({"kind": "missing-revision", "revisions": missing})
        completed = sum(step["state"] == "completed" for step in canonical.steps)
        current_step = next((step for step in canonical.steps if step["state"] == "active"), None)
        if current_step is None:
            current_step = next((step for step in canonical.steps if step["state"] in {"pending", "blocked"}), None)
        stale = False
        age_seconds = None
        if now is not None:
            observed = datetime.fromisoformat(canonical.observed_at.replace("Z", "+00:00"))
            age_seconds = max(0.0, (now - observed).total_seconds())
            stale = current_step is not None and age_seconds > stale_after_seconds
            if stale:
                diagnostics.append({"kind": "stale-plan", "state": "stale", "after_seconds": stale_after_seconds})
        plans.append({
            "event_id": canonical.event_id, "operation_id": identity[2],
            "session_id": identity[0], "repository": identity[1], "issue": identity[3],
            "objective": canonical.objective, "steps": [dict(step) for step in canonical.steps],
            "completed_steps": completed, "total_steps": len(canonical.steps),
            "current_step": dict(current_step) if current_step else None,
            "observed_at": canonical.observed_at, "source": canonical.source,
            "revision": canonical.revision, "revision_reason": canonical.revision_reason,
            "evidence_refs": list(canonical.evidence_refs), "history": history,
            "diagnostics": diagnostics, "stale": stale,
            "age_seconds": age_seconds,
        })
    return tuple(sorted(plans, key=lambda item: (item["observed_at"], item["operation_id"]), reverse=True))


def read_plan_records(path: Path) -> tuple[PlanEvent, ...]:
    if not path.exists():
        return ()
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(PlanEvent.from_dict(json.loads(line)))
    return tuple(records)
