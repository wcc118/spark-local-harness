from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .diagnostics import _safe_atom, _timestamp, append_record, read_records
from .plan_event import _public_text
from .workflow import REPOSITORY_PATTERN


CHECKPOINT_SCHEMA_VERSION = 1
DEFAULT_PACKET_BYTES = 12_000
GIT_SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def _refs(values: tuple[str, ...], name: str, *, required: bool = False) -> None:
    if required and not values:
        raise ValueError(f"{name}s must not be empty")
    for value in values:
        _safe_atom(value, name)


def _relative_path(value: str) -> None:
    if not value or len(value) > 512 or any(ord(char) < 32 for char in value):
        raise ValueError("relevant_file must be a bounded relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value in {".", ""}:
        raise ValueError("relevant_file must be a bounded relative path")


@dataclass(frozen=True)
class ContextCheckpointEvent:
    """Public, durable continuation state captured before compaction."""

    checkpoint_id: str
    observed_at: str
    repository: str
    issue: int
    operation_id: str
    session_id: str
    turn_id: str
    plan_event_id: str
    plan_revision: int
    objective: str
    acceptance_criteria_refs: tuple[str, ...]
    decision_refs: tuple[str, ...]
    test_evidence_refs: tuple[str, ...]
    relevant_files: tuple[str, ...]
    next_action: str
    head_sha: str
    pull_request: int | None = None
    blocker_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    terminal: bool = False
    schema_version: int = CHECKPOINT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in (
            "checkpoint_id", "operation_id", "session_id", "turn_id",
            "plan_event_id",
        ):
            _safe_atom(getattr(self, name), name)
        _timestamp(self.observed_at)
        if not REPOSITORY_PATTERN.fullmatch(self.repository):
            raise ValueError("repository must be a valid OWNER/REPO identifier")
        if self.issue <= 0 or self.plan_revision <= 0:
            raise ValueError("issue and plan_revision must be positive")
        if self.pull_request is not None and self.pull_request <= 0:
            raise ValueError("pull_request must be positive")
        if not GIT_SHA.fullmatch(self.head_sha):
            raise ValueError("head_sha must be an exact lowercase Git object id")
        _public_text(self.objective, "objective")
        _public_text(self.next_action, "next_action")
        _refs(self.acceptance_criteria_refs, "acceptance_criteria_ref", required=True)
        _refs(self.decision_refs, "decision_ref", required=True)
        _refs(self.test_evidence_refs, "test_evidence_ref", required=True)
        _refs(self.blocker_refs, "blocker_ref")
        _refs(self.evidence_refs, "evidence_ref")
        if not self.relevant_files:
            raise ValueError("relevant_files must not be empty")
        for value in self.relevant_files:
            _relative_path(value)
        if self.schema_version != CHECKPOINT_SCHEMA_VERSION:
            raise ValueError("unsupported checkpoint schema version")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ContextCheckpointEvent:
        data = dict(payload)
        for name in (
            "acceptance_criteria_refs", "decision_refs", "test_evidence_refs",
            "relevant_files", "blocker_refs", "evidence_refs",
        ):
            data[name] = tuple(data.get(name, ()))
        return cls(**data)


def checkpoint_packet(
    checkpoint: ContextCheckpointEvent, *, max_bytes: int = DEFAULT_PACKET_BYTES,
) -> tuple[str, str]:
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    encoded = json.dumps(
        checkpoint.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    )
    if len(encoded.encode("utf-8")) > max_bytes:
        raise ValueError("checkpoint packet exceeds configured byte bound")
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def checkpoint_diagnostics(
    checkpoint: ContextCheckpointEvent,
    *,
    repository: str,
    issue: int,
    pull_request: int | None,
    head_sha: str,
    plan_event_id: str,
    plan_revision: int,
    latest_completed_turn_id: str,
) -> tuple[str, ...]:
    mismatches: list[str] = []
    expected = (
        ("repository", checkpoint.repository, repository),
        ("issue", checkpoint.issue, issue),
        ("pull-request", checkpoint.pull_request, pull_request),
        ("head-sha", checkpoint.head_sha, head_sha),
        ("plan-event", checkpoint.plan_event_id, plan_event_id),
        ("plan-revision", checkpoint.plan_revision, plan_revision),
        ("latest-turn", checkpoint.turn_id, latest_completed_turn_id),
    )
    for name, observed, current in expected:
        if observed != current:
            mismatches.append(f"stale-{name}")
    return tuple(mismatches)


def append_checkpoint(path: Path, checkpoint: ContextCheckpointEvent) -> bool:
    return append_record(path, checkpoint)


def read_checkpoints(path: Path) -> tuple[ContextCheckpointEvent, ...]:
    return read_records(path, ContextCheckpointEvent)


def latest_checkpoint(
    checkpoints: Iterable[ContextCheckpointEvent], *, session_id: str,
) -> ContextCheckpointEvent | None:
    matching = [item for item in checkpoints if item.session_id == session_id]
    return max(matching, key=lambda item: (item.observed_at, item.checkpoint_id)) if matching else None
