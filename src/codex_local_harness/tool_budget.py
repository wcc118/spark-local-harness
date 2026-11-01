"""Bounded pure counting slice for tool call budget enforcement.

Tracks unique completed nonempty tool calls from OpenCode stream events.
Supports OpenCode type tool_use part.callID, part.tool, state.status completed/error,
state.input. Ignores pending/running, repeated updates same call, empty input.
Bash command whitespace empty is empty even other fields.

Results may include is_error and still count. Never count identical command
in different callIDs as one; total budget must catch distinct command churn.

CLI: python -m codex_local_harness.tool_budget --log PATH --limit N
     reads JSONL incrementally and prints counts/limit/exceeded.
     Exit 75 if exceeded, 0 if within, 2 malformed JSON/config.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


class ToolCallBudget:
    """Track and enforce budget of unique completed nonempty tool calls."""

    def __init__(self, limit: int) -> None:
        """Initialize with a positive nonbool integer limit."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        self._limit = limit
        self._completed_ids: set[str] = set()
        self._pending_calls: dict[str, dict[str, Any]] = {}

    @property
    def count(self) -> int:
        """Return number of unique completed nonempty tool calls."""
        return len(self._completed_ids)

    @property
    def exceeded(self) -> bool:
        """Return whether count exceeds limit."""
        return self.count > self._limit

    def _is_empty_input(self, input_val: Any) -> bool:
        """Check if input is empty. Empty bash command whitespace is empty."""
        if input_val is None:
            return True
        if isinstance(input_val, dict):
            # For OpenCode events, input is typically a dict
            cmd = input_val.get("command")
            if cmd is None:
                return True
            if isinstance(cmd, str) and cmd.strip() == "":
                return True
            # If command is present and nonempty (or other keys present), not empty
            return False
        if isinstance(input_val, str):
            return input_val.strip() == ""
        return False

    def _get_call_id(self, part: dict[str, Any]) -> str | None:
        """Extract call ID from part, supporting both OpenCode type."""
        # OpenCode type: part.callID (or id)
        call_id = part.get("callID")
        if call_id is None:
            call_id = part.get("id")
        return call_id if isinstance(call_id, str) and call_id else None

    def _get_state_status(self, part: dict[str, Any]) -> str | None:
        """Extract state status from part."""
        state = part.get("state")
        if isinstance(state, dict):
            return state.get("status")
        return None

    def _get_tool(self, part: dict[str, Any]) -> str | None:
        """Extract tool name from part."""
        tool = part.get("tool")
        if isinstance(tool, str) and tool:
            return tool
        return None

    def _get_input(self, part: dict[str, Any]) -> Any:
        """Extract input from part."""
        return part.get("input")

    def _get_part(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """Extract part from event."""
        part = event.get("part")
        if isinstance(part, dict):
            return part
        return None

    def observe(self, event: dict[str, Any]) -> bool:
        """Record event and return whether count exceeds limit.

        Supports:
        - OpenCode tool_use events: type=tool_use, part with callID/tool/state
        - OpenCode tool_result events: type=tool_result with tool_use_id
        - Qwen native: message.content tool_use/tool_result events

        Only count once result is known (completed/error state, or tool_result).
        Ignore pending/running, repeated updates same call, empty input.
        Results with is_error still count.
        """
        if not isinstance(event, dict):
            return self._exceeded

        # Check for OpenCode tool_use event
        if event.get("type") == "tool_use":
            part = self._get_part(event)
            if part is None:
                return self._exceeded

            call_id = self._get_call_id(part)
            if call_id is None:
                return self._exceeded

            status = self._get_state_status(part)
            tool = self._get_tool(part)

            if status in ("completed", "error"):
                # Completed or error - check input and record
                input_val = self._get_input(part)
                if not self._is_empty_input(input_val):
                    self._completed_ids.add(call_id)
                return self._exceeded

            # Pending/running - track for later completion
            if status in ("pending", "running"):
                self._pending_calls[call_id] = {"tool": tool, "input": self._get_input(part)}
                return self._exceeded

            return self._exceeded

        # Check for OpenCode tool_result event
        if event.get("type") == "tool_result":
            # tool_result at top level has tool_use_id
            tool_use_id = event.get("tool_use_id")
            if isinstance(tool_use_id, str) and tool_use_id and tool_use_id in self._pending_calls:
                del self._pending_calls[tool_use_id]
                # tool_result counts as completion
                self._completed_ids.add(tool_use_id)
                return self._exceeded

            # Or nested part structure
            part = self._get_part(event)
            if part is not None:
                tool_use_id = self._get_call_id(part)
                if isinstance(tool_use_id, str) and tool_use_id and tool_use_id in self._pending_calls:
                    del self._pending_calls[tool_use_id]
                    self._completed_ids.add(tool_use_id)
                    return self._exceeded

        # Check for Qwen native assistant message with tool_use in content
        message = event.get("message") or {}
        content = message.get("content")
        if not isinstance(content, list):
            content = []

        for part in content:
            if not isinstance(part, dict):
                continue

            if part.get("type") == "tool_use":
                call_id = part.get("id")
                if isinstance(call_id, str) and call_id:
                    tool = part.get("name")
                    input_val = part.get("input")
                    if tool and not self._is_empty_input(input_val):
                        self._completed_ids.add(call_id)
                        return self._exceeded

            if part.get("type") == "tool_result":
                tool_use_id = part.get("tool_use_id")
                if isinstance(tool_use_id, str) and tool_use_id:
                    # tool_result counts as completion
                    self._completed_ids.add(tool_use_id)
                    return self._exceeded

        return self._exceeded

    @property
    def _exceeded(self) -> bool:
        """Return whether count exceeds limit."""
        return self.count > self._limit


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for tool budget check."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True, help="Path to JSONL log file")
    parser.add_argument("--limit", type=int, required=True, help="Budget limit (positive integer)")

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2

    # Validate limit
    if isinstance(args.limit, bool) or args.limit <= 0:
        return 2

    try:
        log_path = args.log
        if not log_path.exists():
            # Empty log means zero calls, within budget
            print(json.dumps({"count": 0, "limit": args.limit, "exceeded": False}))
            return 0

        budget = ToolCallBudget(args.limit)

        with log_path.open("r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    return 2

                budget.observe(event)

        exceeded = budget._exceeded
        print(json.dumps({"count": budget.count, "limit": args.limit, "exceeded": exceeded}))

        return 75 if exceeded else 0

    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
