from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..health import RunHealth, TokenUsage, append_jsonl


@dataclass(frozen=True)
class OpenCodeStats:
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    turns: int
    tool_calls: int
    tool_errors: int
    wall_seconds: float
    cache_read: int | None = None
    cache_write: int | None = None
    cache_provenance: str | None = None

    def as_health(
        self,
        *,
        run_id: str,
        issue: int,
        role: str,
        model: str,
        profile: str,
        task_class: str,
        status: str,
        failure_class: str | None = None,
        inference_status: int | None = None,
        workflow_status: int | None = None,
        delivery_ready: bool | None = None,
        dirty_entries: int = 0,
        generated_artifacts: int = 0,
        repository: str | None = None,
        pull_request: int | None = None,
        runtime: str = "opencode",
    ) -> RunHealth:
        return RunHealth(
            run_id=run_id,
            issue=issue,
            role=role,
            runtime=runtime,
            model=model,
            profile=profile,
            task_class=task_class,
            status=status,
            wall_seconds=self.wall_seconds,
            usage=TokenUsage(
                local_input=self.input_tokens,
                local_output=self.output_tokens,
                local_reasoning=self.reasoning_tokens,
                local_cache_read=self.cache_read,
                local_cache_write=self.cache_write,
                cache_provenance=self.cache_provenance,
            ),
            failure_class=failure_class,
            turns=self.turns,
            tool_calls=self.tool_calls,
            tool_errors=self.tool_errors,
            inference_status=inference_status,
            workflow_status=workflow_status,
            delivery_ready=delivery_ready,
            dirty_entries=dirty_entries,
            generated_artifacts=generated_artifacts,
            repository=repository,
            pull_request=pull_request,
        )


def parse_log(path: Path, runtime: str = "opencode") -> OpenCodeStats:
    if runtime == "qwen-code":
        return _parse_qwen_log(path)
    input_tokens = output_tokens = reasoning_tokens = turns = 0
    tool_call_ids: set[str] = set()
    tool_error_ids: set[str] = set()
    cache_read = cache_write = 0
    cache_provenance = "unsupported"  # Default when no cache object is found
    timestamps: list[int] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("event is not an object")
                timestamp = event.get("timestamp")
                if isinstance(timestamp, int):
                    timestamps.append(timestamp)
                event_type = event.get("type")
                part = event.get("part") or {}
                if not isinstance(part, dict):
                    raise ValueError("part is not an object")
                if event_type == "step_finish":
                    tokens = part.get("tokens") or {}
                    if not isinstance(tokens, dict):
                        raise ValueError("tokens is not an object")
                    input_tokens += _non_negative_int(tokens.get("input", 0), "input tokens")
                    output_tokens += _non_negative_int(tokens.get("output", 0), "output tokens")
                    reasoning_tokens += _non_negative_int(tokens.get("reasoning", 0), "reasoning tokens")
                    turns += 1
                    # Handle cache tokens if present
                    cache_obj = tokens.get("cache")
                    if isinstance(cache_obj, dict):
                        # Validate and accumulate cache read/write
                        read_val = cache_obj.get("read", 0)
                        write_val = cache_obj.get("write", 0)
                        cache_read += _non_negative_int(read_val, "cache read")
                        cache_write += _non_negative_int(write_val, "cache write")
                        cache_provenance = "observed"
                    elif "cache" in tokens:
                        # Malformed cache data (not a dict or missing fields)
                        raise ValueError(f"invalid cache object at line {line_number}")
                elif event_type == "tool_use":
                    state = part.get("state") or {}
                    if not isinstance(state, dict):
                        raise ValueError("tool state is not an object")
                    call_id = part.get("callID")
                    identity = call_id if isinstance(call_id, str) and call_id else f"line:{line_number}"
                    tool_call_ids.add(identity)
                    if state.get("status") == "error":
                        tool_error_ids.add(identity)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid OpenCode event at line {line_number}: {exc}") from exc
    wall_seconds = (max(timestamps) - min(timestamps)) / 1000 if len(timestamps) > 1 else 0.0
    if cache_provenance != "observed":
        cache_read = cache_write = None
    return OpenCodeStats(
        input_tokens, output_tokens, reasoning_tokens, turns,
        len(tool_call_ids), len(tool_error_ids), wall_seconds,
        cache_read, cache_write, cache_provenance,
    )


def _parse_qwen_log(path: Path) -> OpenCodeStats:
    result: dict[str, Any] | None = None
    tool_calls = tool_errors = 0
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid Qwen Code event at line {line_number}: {exc}") from exc
            if not isinstance(event, dict):
                raise ValueError(f"invalid Qwen Code event at line {line_number}: event is not an object")
            if event.get("type") == "assistant":
                content = (event.get("message") or {}).get("content") or []
                tool_calls += sum(1 for item in content if isinstance(item, dict) and item.get("type") == "tool_use")
            elif event.get("type") == "tool_result" and event.get("is_error"):
                tool_errors += 1
            elif event.get("type") == "result":
                result = event
    if result is None:
        raise ValueError("Qwen Code log has no result event")
    usage = result.get("usage") or {}
    return OpenCodeStats(
        _non_negative_int(usage.get("input_tokens", 0), "input tokens"),
        _non_negative_int(usage.get("output_tokens", 0), "output tokens"),
        0,
        _non_negative_int(result.get("num_turns", 0), "turns"),
        tool_calls,
        tool_errors,
        _non_negative_int(result.get("duration_ms", 0), "duration") / 1000,
        _non_negative_int(usage.get("cache_read_input_tokens", 0), "cache read"),
        None,
        "observed",
    )


def _non_negative_int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Normalize an OpenCode log into harness health telemetry")
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--runtime", choices=("opencode", "qwen-code"), default="opencode")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--task-class", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--failure-class")
    parser.add_argument("--inference-status", type=int)
    parser.add_argument("--workflow-status", type=int)
    parser.add_argument("--delivery-ready", choices=("true", "false", "unknown"), default="unknown")
    parser.add_argument("--dirty-entries", type=int, default=0)
    parser.add_argument("--generated-artifacts", type=int, default=0)
    parser.add_argument("--repository")
    parser.add_argument("--pull-request", type=int)
    parser.add_argument("--allow-malformed", action="store_true")
    args = parser.parse_args(argv)
    try:
        try:
            stats = parse_log(args.log, args.runtime)
        except ValueError:
            if not args.allow_malformed:
                raise
            stats = OpenCodeStats(0, 0, 0, 0, 0, 0, 0.0, None, None, "unsupported")
        health = stats.as_health(
            run_id=args.run_id,
            issue=args.issue,
            role=args.role,
            model=args.model,
            profile=args.profile,
            task_class=args.task_class,
            status=args.status,
            failure_class=args.failure_class,
            inference_status=args.inference_status,
            workflow_status=args.workflow_status,
            delivery_ready={"true": True, "false": False, "unknown": None}[args.delivery_ready],
            dirty_entries=args.dirty_entries,
            generated_artifacts=args.generated_artifacts,
            repository=args.repository,
            pull_request=args.pull_request,
            runtime=args.runtime,
        )
        append_jsonl(args.records, health)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(health.as_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
