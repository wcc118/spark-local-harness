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
    ) -> RunHealth:
        return RunHealth(
            run_id=run_id,
            issue=issue,
            role=role,
            runtime="opencode",
            model=model,
            profile=profile,
            task_class=task_class,
            status=status,
            wall_seconds=self.wall_seconds,
            usage=TokenUsage(
                local_input=self.input_tokens,
                local_output=self.output_tokens,
                local_reasoning=self.reasoning_tokens,
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
        )


def parse_log(path: Path) -> OpenCodeStats:
    input_tokens = output_tokens = reasoning_tokens = turns = tool_calls = tool_errors = 0
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
                elif event_type == "tool_use":
                    tool_calls += 1
                    state = part.get("state") or {}
                    if not isinstance(state, dict):
                        raise ValueError("tool state is not an object")
                    if state.get("status") == "error":
                        tool_errors += 1
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid OpenCode event at line {line_number}: {exc}") from exc
    wall_seconds = (max(timestamps) - min(timestamps)) / 1000 if len(timestamps) > 1 else 0.0
    return OpenCodeStats(input_tokens, output_tokens, reasoning_tokens, turns, tool_calls, tool_errors, wall_seconds)


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
    parser.add_argument("--profile", required=True)
    parser.add_argument("--task-class", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--failure-class")
    parser.add_argument("--inference-status", type=int)
    parser.add_argument("--workflow-status", type=int)
    parser.add_argument("--delivery-ready", choices=("true", "false", "unknown"), default="unknown")
    parser.add_argument("--dirty-entries", type=int, default=0)
    parser.add_argument("--generated-artifacts", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        health = parse_log(args.log).as_health(
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
        )
        append_jsonl(args.records, health)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(health.as_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
