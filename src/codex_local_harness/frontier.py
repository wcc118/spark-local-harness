from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .diagnostics import DIAGNOSTIC_SCHEMA_VERSION, _safe_atom, _timestamp


SOURCES = {"measured", "operator-reported", "unavailable"}


@dataclass(frozen=True)
class FrontierStatus:
    status_id: str
    observed_at: str
    source: str
    context_used: int | None = None
    context_window: int | None = None
    plan_remaining_percent: float | None = None
    plan_refresh_at: str | None = None
    note_code: str | None = None
    schema_version: int = DIAGNOSTIC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _safe_atom(self.status_id, "status_id")
        _timestamp(self.observed_at)
        if self.source not in SOURCES:
            raise ValueError("invalid frontier telemetry source")
        for name, value in (("context_used", self.context_used), ("context_window", self.context_window)):
            if value is not None and value < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.context_used is not None and self.context_window is not None and self.context_used > self.context_window:
            raise ValueError("context_used cannot exceed context_window")
        if self.plan_remaining_percent is not None and not 0 <= self.plan_remaining_percent <= 100:
            raise ValueError("plan_remaining_percent must be between 0 and 100")
        if self.plan_refresh_at is not None:
            _timestamp(self.plan_refresh_at)
        if self.note_code is not None:
            _safe_atom(self.note_code, "note_code")
        if self.source == "unavailable" and any(value is not None for value in (self.context_used, self.context_window, self.plan_remaining_percent, self.plan_refresh_at)):
            raise ValueError("unavailable telemetry cannot contain inferred values")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FrontierStatus:
        return cls(**payload)


def latest_status(records: Iterable[FrontierStatus]) -> FrontierStatus | None:
    items = tuple(records)
    return max(items, key=lambda item: (item.observed_at, item.status_id)) if items else None
