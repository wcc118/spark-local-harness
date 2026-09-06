from __future__ import annotations

import argparse
import json
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .diagnostics import DIAGNOSTIC_SCHEMA_VERSION, _safe_atom, _timestamp, append_record, read_records
from .workflow import REPOSITORY_PATTERN


TOKEN_COUNT_DISCRIMINATOR = re.compile(r'"type"\s*:\s*"token_count"')
PHASES = {"start", "finish"}
COUNTER_FIELDS = (
    "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
    "output_tokens", "reasoning_output_tokens", "total_tokens",
)


@dataclass(frozen=True)
class OperationUsageSnapshot:
    record_id: str
    observed_at: str
    phase: str
    session_id: str
    repository: str
    issue: int
    operation_id: str
    source: str
    input_tokens: int
    cached_input_tokens: int
    cache_write_input_tokens: int
    output_tokens: int
    reasoning_output_tokens: int
    total_tokens: int
    context_window: int
    last_input_tokens: int | None = None
    schema_version: int = DIAGNOSTIC_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("record_id", "session_id", "operation_id", "source"):
            _safe_atom(getattr(self, name), name)
        _timestamp(self.observed_at)
        if self.phase not in PHASES:
            raise ValueError("phase must be start or finish")
        if not REPOSITORY_PATTERN.fullmatch(self.repository):
            raise ValueError("repository must be a valid OWNER/REPO identifier")
        if self.issue <= 0:
            raise ValueError("issue must be positive")
        for name in (*COUNTER_FIELDS, "context_window"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.last_input_tokens is not None and (not isinstance(self.last_input_tokens, int) or isinstance(self.last_input_tokens, bool) or self.last_input_tokens < 0):
            raise ValueError("last_input_tokens must be a non-negative integer or unavailable")
        if self.schema_version != DIAGNOSTIC_SCHEMA_VERSION:
            raise ValueError("unsupported operation usage schema version")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> OperationUsageSnapshot:
        return cls(**payload)


def find_rollout(session_root: Path, thread_id: str) -> Path:
    try:
        parsed_thread = uuid.UUID(thread_id)
    except (ValueError, AttributeError) as exc:
        raise ValueError("thread id must be a UUID") from exc
    if str(parsed_thread) != thread_id.lower():
        raise ValueError("thread id must be a UUID")
    matches = tuple(session_root.glob(f"**/rollout-*-{thread_id}.jsonl"))
    if len(matches) != 1:
        raise ValueError(f"expected exactly one rollout for thread {thread_id}; found {len(matches)}")
    return matches[0]


def read_latest_token_count(path: Path) -> tuple[str, dict[str, int | None]]:
    latest: tuple[str, dict[str, int | None]] | None = None
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            # Privacy boundary: do not decode unrelated event lines.
            if not TOKEN_COUNT_DISCRIMINATOR.search(line):
                continue
            event = json.loads(line)
            payload = event.get("payload")
            if not isinstance(payload, dict) or payload.get("type") != "token_count":
                continue
            info = payload.get("info")
            usage = info.get("total_token_usage") if isinstance(info, dict) else None
            if not isinstance(usage, dict):
                continue
            values: dict[str, int | None] = {}
            for field in COUNTER_FIELDS:
                value = usage.get(field)
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ValueError(f"invalid {field} in token_count event")
                values[field] = value
            context_window = info.get("model_context_window")
            if not isinstance(context_window, int) or isinstance(context_window, bool) or context_window <= 0:
                raise ValueError("invalid model_context_window in token_count event")
            values["context_window"] = context_window
            # Extract last token usage separately (available from tokenUsage/updated events)
            last = info.get("last") if isinstance(info, dict) else None
            if isinstance(last, dict):
                last_input = last.get("inputTokens")
                if isinstance(last_input, int) and not isinstance(last_input, bool) and last_input >= 0:
                    values["last_input_tokens"] = last_input
                else:
                    values["last_input_tokens"] = None
            else:
                values["last_input_tokens"] = None
            observed_at = event.get("timestamp")
            if not isinstance(observed_at, str):
                raise ValueError("token_count timestamp is missing")
            _timestamp(observed_at)
            latest = (observed_at, values)
    if latest is None:
        raise ValueError("no token_count event found")
    return latest


def capture_snapshot(
    *, session_root: Path, thread_id: str, phase: str, repository: str,
    issue: int, operation_id: str, record_id: str,
) -> OperationUsageSnapshot:
    observed_at, values = read_latest_token_count(find_rollout(session_root, thread_id))
    return OperationUsageSnapshot(
        record_id=record_id, observed_at=observed_at, phase=phase,
        session_id=thread_id, repository=repository, issue=issue,
        operation_id=operation_id, source="codex-rollout-token-count",
        **{k: v for k, v in values.items() if k != "last_input_tokens"},
        last_input_tokens=values.get("last_input_tokens"),
    )


def derive_usage(snapshots: Iterable[OperationUsageSnapshot]) -> tuple[dict[str, Any], ...]:
    grouped: dict[tuple[str, str, int, str], list[OperationUsageSnapshot]] = {}
    for item in snapshots:
        grouped.setdefault((item.session_id, item.repository, item.issue, item.operation_id), []).append(item)
    results: list[dict[str, Any]] = []
    for key, items in grouped.items():
        starts = [item for item in items if item.phase == "start"]
        finishes = [item for item in items if item.phase == "finish"]
        start = min(starts, key=lambda item: (item.observed_at, item.record_id)) if starts else None
        finish = max(finishes, key=lambda item: (item.observed_at, item.record_id)) if finishes else None
        result: dict[str, Any] = {
            "session_id": key[0], "repository": key[1], "issue": key[2],
            "operation_id": key[3], "status": "unavailable", "provenance": "observed",
            "observed_at": finish.observed_at if finish else (start.observed_at if start else None),
        }
        if start is None or finish is None:
            result["diagnostic"] = "missing-start" if start is None else "missing-finish"
        elif start.context_window != finish.context_window:
            result["diagnostic"] = "context-window-changed"
        else:
            deltas = {field: getattr(finish, field) - getattr(start, field) for field in COUNTER_FIELDS}
            if any(value < 0 for value in deltas.values()):
                result["diagnostic"] = "counter-reset"
            else:
                uncached = deltas["input_tokens"] - deltas["cached_input_tokens"]
                if uncached < 0:
                    result["diagnostic"] = "cache-exceeds-input"
                else:
                    result.update(deltas)
                    result["uncached_input_tokens"] = uncached
                    result["context_window"] = finish.context_window
                    result["status"] = "observed"
                    # Compute pressure metrics using last input tokens
                    if finish.last_input_tokens is not None and finish.context_window > 0:
                        result["last_input_tokens"] = finish.last_input_tokens
                        result["context_pressure"] = finish.last_input_tokens / finish.context_window
                        result["context_headroom"] = 1.0 - result["context_pressure"]
                    else:
                        result["last_input_tokens"] = None
                        result["context_pressure"] = None
                        result["context_headroom"] = None
        results.append(result)
    return tuple(sorted(results, key=lambda item: (item["observed_at"] or "", item["operation_id"]), reverse=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture privacy-minimal Codex operation token counters")
    parser.add_argument("phase", choices=sorted(PHASES))
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--session-root", type=Path, required=True)
    parser.add_argument("--thread-id", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--record-id", required=True)
    args = parser.parse_args(argv)
    try:
        record = capture_snapshot(
            session_root=args.session_root, thread_id=args.thread_id, phase=args.phase,
            repository=args.repository, issue=args.issue, operation_id=args.operation_id,
            record_id=args.record_id,
        )
        append_record(args.records, record)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(record.as_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
