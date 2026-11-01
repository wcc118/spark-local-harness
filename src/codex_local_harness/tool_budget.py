"""Bounded pure counting slice for tool call budget enforcement.

Tracks unique completed nonempty tool calls from OpenCode stream events.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


class ToolCallBudget:
    """Track and enforce budget of unique completed nonempty tool calls."""

    def __init__(self, limit: int) -> None:
        """Initialize with a positive nonbool integer limit."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        self._limit = limit
        self._completed_ids: set[str] = set()
        self._pending_calls: dict[str, object] = {}

    @property
    def count(self) -> int:
        """Return number of unique completed nonempty tool calls."""
        return len(self._completed_ids)

    @property
    def exceeded(self) -> bool:
        """Return whether count exceeds limit."""
        return self.count > self._limit

    def _is_empty_input(self, input_val: object, tool: str | None = None) -> bool:
        """Check if input is empty. Bash requires command key."""
        if input_val is None:
            return True
        if isinstance(input_val, dict):
            if not input_val:
                return True
            cmd = input_val.get("command")
            if tool == "bash":
                if cmd is None:
                    return True
                if isinstance(cmd, str) and cmd.strip() == "":
                    return True
                return False
            return False
        if isinstance(input_val, str):
            return input_val.strip() == ""
        return True

    def _get_call_id(self, part: dict[str, object]) -> str | None:
        """Extract call ID from part."""
        call_id = part.get("callID")
        if call_id is None:
            call_id = part.get("id")
        return call_id if isinstance(call_id, str) and call_id else None

    def _get_tool(self, part: dict[str, object]) -> str | None:
        """Extract tool name from part."""
        tool = part.get("tool")
        if isinstance(tool, str) and tool:
            return tool
        return None

    def _get_state_input(self, part: dict[str, object]) -> object:
        """Extract input from part.state."""
        state = part.get("state")
        if isinstance(state, dict):
            return state.get("input")
        return None

    def _count_call(self, call_id: str, input_val: object, tool: str | None = None) -> None:
        """Count a call if not already counted and input is nonempty."""
        if call_id in self._completed_ids:
            return
        if input_val is not None and not self._is_empty_input(input_val, tool):
            self._completed_ids.add(call_id)

    def observe(self, event: dict[str, object]) -> bool:
        """Record event and return whether count exceeds limit."""
        if not isinstance(event, dict):
            return self.exceeded

        event_type = event.get("type")
        if event_type == "tool_use":
            part = event.get("part")
            if isinstance(part, dict):
                call_id = self._get_call_id(part)
                if call_id is None:
                    return self.exceeded
                tool = self._get_tool(part)
                status = part.get("state", {}).get("status") if isinstance(part.get("state"), dict) else None
                input_val = self._get_state_input(part)

                if status in ("completed", "error"):
                    self._count_call(call_id, input_val, tool)
                elif status in ("pending", "running"):
                    self._pending_calls[call_id] = (input_val, tool)
                else:
                    self._pending_calls[call_id] = (input_val, tool)
            return self.exceeded

        if event_type == "tool_result":
            tool_use_id = event.get("tool_use_id")
            if isinstance(tool_use_id, str) and tool_use_id:
                if tool_use_id not in self._completed_ids:
                    val = self._pending_calls.pop(tool_use_id, None)
                    if val is not None:
                        input_val, tool = val if isinstance(val, tuple) else (val, None)
                        if input_val is not None and not self._is_empty_input(input_val, tool):
                            self._completed_ids.add(tool_use_id)

            part = event.get("part")
            if isinstance(part, dict):
                call_id = self._get_call_id(part)
                if call_id and call_id not in self._completed_ids:
                    val = self._pending_calls.pop(call_id, None)
                    if val is not None:
                        input_val, tool = val if isinstance(val, tuple) else (val, None)
                        if input_val is not None and not self._is_empty_input(input_val, tool):
                            self._completed_ids.add(call_id)
            return self.exceeded

        message = event.get("message")
        if not isinstance(message, dict):
            return self.exceeded

        content = message.get("content")
        if isinstance(content, str):
            return self.exceeded
        if not isinstance(content, list):
            return self.exceeded

        for item in content:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "tool_use":
                call_id = item.get("id")
                if isinstance(call_id, str) and call_id:
                    input_val = item.get("input")
                    tool = item.get("name")
                    self._pending_calls[call_id] = (input_val, tool)
            if item_type == "tool_result":
                tool_use_id = item.get("tool_use_id")
                if isinstance(tool_use_id, str) and tool_use_id:
                    if tool_use_id not in self._completed_ids:
                        val = self._pending_calls.pop(tool_use_id, None)
                        if val is not None:
                            input_val, tool = val if isinstance(val, tuple) else (val, None)
                            if input_val is not None and not self._is_empty_input(input_val, tool):
                                self._completed_ids.add(tool_use_id)

        return self.exceeded


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for tool budget check."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True, help="Path to JSONL log file")
    parser.add_argument("--limit", type=int, required=True, help="Budget limit (positive integer)")

    args = parser.parse_args(argv)

    if isinstance(args.limit, bool) or args.limit <= 0:
        return 2

    log_path = args.log
    if not log_path.exists():
        return 2

    try:
        budget = ToolCallBudget(args.limit)

        with log_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    return 2

                budget.observe(event)

        if budget.exceeded:
            return 75
        return 0

    except (OSError, ValueError):
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
