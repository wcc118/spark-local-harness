from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import fcntl


SCHEMA_VERSION = 1
INTERVENTION_DEPTHS = {"unknown", "none", "audit", "targeted", "takeover"}
TOKEN_PROVENANCE = {"observed", "unsupported", "estimated", "not-applicable"}


@dataclass(frozen=True)
class TokenUsage:
    local_input: int = 0
    local_output: int = 0
    local_reasoning: int = 0
    frontier_input: int = 0
    frontier_output: int = 0
    frontier_reasoning: int = 0
    local_cache_read: int | None = None
    local_cache_write: int | None = None
    cache_provenance: str | None = None

    def __post_init__(self) -> None:
        if any(value < 0 for value in asdict(self).values() if isinstance(value, int)):
            raise ValueError("token counts must be non-negative")
        if self.cache_provenance is not None and self.cache_provenance not in TOKEN_PROVENANCE:
            raise ValueError(f"cache_provenance must be one of {TOKEN_PROVENANCE}")

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
    turns: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    inference_status: int | None = None
    workflow_status: int | None = None
    delivery_ready: bool | None = None
    dirty_entries: int = 0
    generated_artifacts: int = 0
    repository: str | None = None
    pull_request: int | None = None
    schema_version: int = SCHEMA_VERSION
    canonical_usage: dict[str, Any] | None = None
    lane_admission: str | None = None
    loaded_context: int | None = None

    @property
    def local_tokens(self) -> int | None:
        if self.canonical_usage is None:
            return self.usage.local_total
        return (self.canonical_usage.get("total_including_cache")
                if self.canonical_usage.get("coverage") == "complete" else None)

    @property
    def frontier_tokens(self) -> int | None:
        # Candidate assessments are separate session intervals, not inferred
        # per-worker attribution. Legacy records retain their historical values.
        return None if self.canonical_usage is not None else self.usage.frontier_total

    def __post_init__(self) -> None:
        required = (self.run_id, self.role, self.runtime, self.model, self.profile, self.task_class, self.status)
        if any(not value.strip() for value in required):
            raise ValueError("required string fields must be non-empty")
        if self.loaded_context is not None and (isinstance(self.loaded_context, bool) or not isinstance(self.loaded_context, int) or self.loaded_context < 0):
            raise ValueError("loaded context must be a non-negative integer")
        if self.lane_admission is not None and (not isinstance(self.lane_admission, str) or not self.lane_admission.strip()):
            raise ValueError("lane admission identity must be non-empty")
        if self.issue <= 0:
            raise ValueError("issue must be positive")
        if self.pull_request is not None and self.pull_request <= 0:
            raise ValueError("pull request must be positive")
        if self.repository is not None and (
            "/" not in self.repository or any(character.isspace() for character in self.repository)
        ):
            raise ValueError("repository must be an OWNER/REPO identifier")
        if self.wall_seconds < 0 or self.correction_cycle < 0:
            raise ValueError("timing and correction counts must be non-negative")
        if self.intervention_depth not in INTERVENTION_DEPTHS:
            raise ValueError("invalid intervention depth")
        if min(
            self.scout_findings,
            self.useful_scout_findings,
            self.turns,
            self.tool_calls,
            self.tool_errors,
            self.dirty_entries,
            self.generated_artifacts,
        ) < 0:
            raise ValueError("finding and activity counts must be non-negative")
        for value in (self.inference_status, self.workflow_status):
            if value is not None and value < 0:
                raise ValueError("process statuses must be non-negative")
        if self.useful_scout_findings > self.scout_findings:
            raise ValueError("useful findings cannot exceed total findings")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported health schema version")

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["usage"]["local_total"] = self.local_tokens
        result["usage"]["frontier_total"] = self.frontier_tokens
        result["usage"]["total"] = (self.local_tokens + self.frontier_tokens
                                    if self.local_tokens is not None and self.frontier_tokens is not None else None)
        return result

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RunHealth:
        data = dict(payload)
        usage_data = dict(data.pop("usage"))
        usage_data.pop("local_total", None)
        usage_data.pop("frontier_total", None)
        usage_data.pop("total", None)
        # Default cache provenance to "unsupported" only if the key is completely missing
        # (backward compatibility for old records that don't have cache fields)
        if "cache_provenance" not in usage_data:
            usage_data["cache_provenance"] = "unsupported"
        return cls(usage=TokenUsage(**usage_data), **data)


def append_jsonl(path: Path, record: RunHealth) -> bool:
    """Append one canonical run, rejecting conflicting reuse of its identity."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(record.as_dict(), sort_keys=True, separators=(",", ":")) + "\n"
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                existing = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid health record at line {line_number}: {exc}") from exc
            if existing.get("run_id") == record.run_id:
                if existing != record.as_dict():
                    raise ValueError(f"conflicting health record for run_id {record.run_id}")
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                return False
        handle.seek(0, 2)
        handle.write(encoded)
        handle.flush()
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    return True


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
    local_tokens = sum(item.local_tokens or 0 for item in items)
    unknown_usage = sum(item.local_tokens is None for item in items)
    unknown_frontier = sum(item.frontier_tokens is None for item in items)
    frontier_tokens = sum(item.usage.frontier_total for item in items)
    total_tokens = local_tokens + frontier_tokens
    locally_autonomous = sum(
        item.intervention_depth == "none" and item.usage.frontier_total == 0 for item in items
    )
    delivery_observations = tuple(item for item in items if item.delivery_ready is not None)
    return {
        "schema_version": SCHEMA_VERSION,
        "runs": len(items),
        "status_counts": _counts(item.status for item in items),
        "outcome_counts": _counts(item.outcome for item in items),
        "failure_counts": _counts(item.failure_class for item in items if item.failure_class),
        "local_tokens": None if unknown_usage else local_tokens,
        "known_local_tokens": local_tokens,
        "unknown_usage_runs": unknown_usage,
        "partial_usage_runs": sum(bool(item.canonical_usage) and item.canonical_usage["coverage"] == "partial" for item in items),
        "unknown_frontier_runs": unknown_frontier,
        "frontier_tokens": None if unknown_frontier else frontier_tokens,
        "known_frontier_tokens": frontier_tokens,
        "frontier_token_share": None if unknown_frontier or unknown_usage else (frontier_tokens / total_tokens if total_tokens else 0.0),
        "local_autonomy_rate": locally_autonomous / len(items) if items else 0.0,
        "codex_targeted_runs": sum(item.intervention_depth == "targeted" for item in items),
        "codex_takeovers": sum(item.intervention_depth == "takeover" for item in items),
        "correction_cycles": sum(item.correction_cycle for item in items),
        "scout_findings": sum(item.scout_findings for item in items),
        "useful_scout_findings": sum(item.useful_scout_findings for item in items),
        "turns": sum(item.turns for item in items),
        "tool_calls": sum(item.tool_calls for item in items),
        "tool_errors": sum(item.tool_errors for item in items),
        "delivery_ready_rate": (
            sum(item.delivery_ready is True for item in delivery_observations) / len(delivery_observations)
            if delivery_observations else 0.0
        ),
        "delivery_observations": len(delivery_observations),
        "dirty_entries": sum(item.dirty_entries for item in items),
        "generated_artifacts": sum(item.generated_artifacts for item in items),
        "median_wall_seconds": statistics.median(item.wall_seconds for item in items) if items else 0.0,
    }


def _counts(values: Iterable[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))
