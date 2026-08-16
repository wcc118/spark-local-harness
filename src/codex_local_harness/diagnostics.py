from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, TypeVar

import fcntl


DIAGNOSTIC_SCHEMA_VERSION = 1
ADJUDICATION_VERDICTS = {"passed", "failed", "interrupted", "cancelled", "timed-out", "inconclusive"}
ADJUDICATION_SOURCES = {"evaluator", "codex", "operator", "reviewer"}
LIFECYCLE_EVENTS = {"started", "completed", "interrupted", "cancelled", "timed-out"}
INTERVENTION_DEPTHS = {"none", "audit", "targeted", "takeover"}
SAFE_ATOM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/#@+\-]{0,255}\Z")


def _required(value: str, name: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{name} must be non-empty")


def _timestamp(value: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("observed_at must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError("observed_at must include a timezone")


def _safe_atom(value: str, name: str) -> None:
    _required(value, name)
    if not SAFE_ATOM.fullmatch(value):
        raise ValueError(f"{name} must be a bounded identifier")


@dataclass(frozen=True)
class Adjudication:
    adjudication_id: str
    run_id: str
    verdict: str
    source: str
    observed_at: str
    failure_class: str | None = None
    correction_of: str | None = None
    correction_cycle: int = 0
    intervention_depth: str = "none"
    codex_decision: str | None = None
    evidence_refs: tuple[str, ...] = ()
    schema_version: int = DIAGNOSTIC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _safe_atom(self.adjudication_id, "adjudication_id")
        _safe_atom(self.run_id, "run_id")
        _timestamp(self.observed_at)
        if self.verdict not in ADJUDICATION_VERDICTS:
            raise ValueError("invalid adjudication verdict")
        if self.source not in ADJUDICATION_SOURCES:
            raise ValueError("invalid adjudication source")
        if self.intervention_depth not in INTERVENTION_DEPTHS:
            raise ValueError("invalid intervention depth")
        if self.correction_cycle < 0:
            raise ValueError("correction_cycle must be non-negative")
        for name, value in (
            ("failure_class", self.failure_class),
            ("correction_of", self.correction_of),
            ("codex_decision", self.codex_decision),
        ):
            if value is not None:
                _safe_atom(value, name)
        for reference in self.evidence_refs:
            _safe_atom(reference, "evidence_ref")
        if self.schema_version != DIAGNOSTIC_SCHEMA_VERSION:
            raise ValueError("unsupported diagnostic schema version")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Adjudication:
        data = dict(payload)
        data["evidence_refs"] = tuple(data.get("evidence_refs", ()))
        return cls(**data)


@dataclass(frozen=True)
class LifecycleEvent:
    event_id: str
    run_id: str
    event: str
    observed_at: str
    wall_seconds: float | None = None
    exit_status: int | None = None
    dirty_entries: int | None = None
    issue: int | None = None
    role: str | None = None
    model: str | None = None
    profile: str | None = None
    task_class: str | None = None
    schema_version: int = DIAGNOSTIC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _safe_atom(self.event_id, "event_id")
        _safe_atom(self.run_id, "run_id")
        _timestamp(self.observed_at)
        if self.event not in LIFECYCLE_EVENTS:
            raise ValueError("invalid lifecycle event")
        for name, value in (
            ("wall_seconds", self.wall_seconds),
            ("exit_status", self.exit_status),
            ("dirty_entries", self.dirty_entries),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.issue is not None and self.issue <= 0:
            raise ValueError("issue must be positive")
        for name, value in (
            ("role", self.role), ("model", self.model), ("profile", self.profile), ("task_class", self.task_class),
        ):
            if value is not None:
                _safe_atom(value, name)
        if self.schema_version != DIAGNOSTIC_SCHEMA_VERSION:
            raise ValueError("unsupported diagnostic schema version")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> LifecycleEvent:
        return cls(**payload)


@dataclass(frozen=True)
class PublicationFailure:
    failure_id: str
    run_id: str
    phase: str
    target: str
    error_class: str
    observed_at: str
    schema_version: int = DIAGNOSTIC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name, value in (
            ("failure_id", self.failure_id),
            ("run_id", self.run_id),
            ("phase", self.phase),
            ("target", self.target),
            ("error_class", self.error_class),
        ):
            _safe_atom(value, name)
        _timestamp(self.observed_at)
        if self.schema_version != DIAGNOSTIC_SCHEMA_VERSION:
            raise ValueError("unsupported diagnostic schema version")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> PublicationFailure:
        return cls(**payload)


Record = TypeVar("Record", Adjudication, LifecycleEvent, PublicationFailure)


def append_record(path: Path, record: Any) -> bool:
    """Append one canonical fact; exact replay is a no-op and conflicts fail."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(record.as_dict(), sort_keys=True, separators=(",", ":")) + "\n"
    identity_name = next(
        (name for name in ("adjudication_id", "event_id", "status_id", "failure_id") if hasattr(record, name)),
        None,
    )
    if identity_name is None:
        raise ValueError("record has no canonical identity")
    identity = getattr(record, identity_name)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                existing = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid diagnostic record at line {line_number}: {exc}") from exc
            if existing.get(identity_name) == identity:
                if existing != record.as_dict():
                    raise ValueError(f"conflicting diagnostic record for {identity_name} {identity}")
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                return False
        handle.seek(0, 2)
        handle.write(encoded)
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return True


def read_records(path: Path, record_type: type[Record]) -> tuple[Record, ...]:
    if not path.exists():
        return ()
    records: list[Record] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise TypeError("record is not an object")
            records.append(record_type.from_dict(payload))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid diagnostic record at line {line_number}: {exc}") from exc
    return tuple(records)


def latest_adjudications(records: Iterable[Adjudication]) -> dict[str, Adjudication]:
    """Derive current judgments without mutating or collapsing the source ledger."""
    latest: dict[str, Adjudication] = {}
    for record in records:
        previous = latest.get(record.run_id)
        if previous is None or (record.observed_at, record.adjudication_id) > (
            previous.observed_at,
            previous.adjudication_id,
        ):
            latest[record.run_id] = record
    return latest
