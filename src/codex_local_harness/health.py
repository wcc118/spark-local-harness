from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import fcntl


SCHEMA_VERSION = 1
INTERVENTION_DEPTHS = {"none", "audit", "targeted", "takeover"}


@dataclass(frozen=True)
class TokenUsage:
    local_input: int = 0
    local_output: int = 0
    local_reasoning: int = 0
    frontier_input: int = 0
    frontier_output: int = 0
    frontier_reasoning: int = 0

    def __post_init__(self) -> None:
        if any(value < 0 for value in asdict(self).values()):
            raise ValueError("token counts must be non-negative")

    @property
    def local_total(self) -> int:
        return self.local_input + self.local_output + self.local_reasoning

    @property
    def frontier_total(self) -> int:
        return self.frontier_input + self.frontier_output + self.frontier_reasoning

    @property
    def total(self) -> int:
        return self.local_total + self.frontier_total


@dataclass(frozen=True)
class RunHealth:
    run_id: str
    issue: int
    role: str
    runtime: str
    model: str
    profile: str
    task_class: str
    status: str
    wall_seconds: float
    usage: TokenUsage = TokenUsage()
    correction_cycle: int = 0
    intervention_depth: str = "none"
    codex_decision: str | None = None
    failure_class: str | None = None
    outcome: str = "unknown"
    scout_findings: int = 0
    useful_scout_findings: int = 0
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        required = (self.run_id, self.role, self.runtime, self.model, self.profile, self.task_class, self.status)
        if any(not value.strip() for value in required):
            raise ValueError("required string fields must be non-empty")
        if self.issue <= 0:
            raise ValueError("issue must be positive")
        if self.wall_seconds < 0 or self.correction_cycle < 0:
            raise ValueError("timing and correction counts must be non-negative")
        if self.intervention_depth not in INTERVENTION_DEPTHS:
            raise ValueError("invalid intervention depth")
        if self.scout_findings < 0 or self.useful_scout_findings < 0:
            raise ValueError("finding counts must be non-negative")
        if self.useful_scout_findings > self.scout_findings:
            raise ValueError("useful findings cannot exceed total findings")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported health schema version")

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["usage"]["local_total"] = self.usage.local_total
        result["usage"]["frontier_total"] = self.usage.frontier_total
        result["usage"]["total"] = self.usage.total
        return result

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RunHealth:
        data = dict(payload)
        usage_data = dict(data.pop("usage"))
        usage_data.pop("local_total", None)
        usage_data.pop("frontier_total", None)
        usage_data.pop("total", None)
        return cls(usage=TokenUsage(**usage_data), **data)


def append_jsonl(path: Path, record: RunHealth) -> None:
    """Append one normalized record without ever storing prompts or credentials."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(record.as_dict(), sort_keys=True, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(encoded)
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def read_jsonl(path: Path) -> tuple[RunHealth, ...]:
    if not path.exists():
        return ()
    records: list[RunHealth] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append(RunHealth.from_dict(json.loads(line)))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid health record at line {line_number}: {exc}") from exc
    return tuple(records)


def summarize(records: Iterable[RunHealth]) -> dict[str, Any]:
    items = tuple(records)
    local_tokens = sum(item.usage.local_total for item in items)
    frontier_tokens = sum(item.usage.frontier_total for item in items)
    total_tokens = local_tokens + frontier_tokens
    locally_autonomous = sum(
        item.intervention_depth == "none" and item.usage.frontier_total == 0 for item in items
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "runs": len(items),
        "status_counts": _counts(item.status for item in items),
        "outcome_counts": _counts(item.outcome for item in items),
        "failure_counts": _counts(item.failure_class for item in items if item.failure_class),
        "local_tokens": local_tokens,
        "frontier_tokens": frontier_tokens,
        "frontier_token_share": frontier_tokens / total_tokens if total_tokens else 0.0,
        "local_autonomy_rate": locally_autonomous / len(items) if items else 0.0,
        "codex_targeted_runs": sum(item.intervention_depth == "targeted" for item in items),
        "codex_takeovers": sum(item.intervention_depth == "takeover" for item in items),
        "correction_cycles": sum(item.correction_cycle for item in items),
        "scout_findings": sum(item.scout_findings for item in items),
        "useful_scout_findings": sum(item.useful_scout_findings for item in items),
        "median_wall_seconds": statistics.median(item.wall_seconds for item in items) if items else 0.0,
    }


def _counts(values: Iterable[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))
