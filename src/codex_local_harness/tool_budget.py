"""Count completed tool calls without retaining arguments or tool output."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def nonempty(value: object, tool: object) -> bool:
    if tool in ("bash", "run_shell_command"):
        command = value.get("command") if isinstance(value, dict) else None
        return isinstance(command, str) and bool(command.strip())
    return bool(value) if isinstance(value, dict) else isinstance(value, str) and bool(value.strip())


class ToolCallBudget:
    """Deduplicate completion updates; issuance alone does not consume budget."""

    def __init__(self, limit: int):
        if type(limit) is not int or limit <= 0:
            raise ValueError("limit must be a positive integer")
        self.limit = limit
        self._completed_ids: set[str] = set()
        self._pending_calls: dict[str, bool] = {}

    @property
    def count(self) -> int:
        return len(self._completed_ids)

    @property
    def exceeded(self) -> bool:
        return self.count > self.limit

    def _complete(self, identity: object, eligible: bool | None = None) -> None:
        if not isinstance(identity, str) or not identity:
            return
        pending = self._pending_calls.pop(identity, False)
        if pending if eligible is None else eligible:
            self._completed_ids.add(identity)

    def _pending(self, identity: object, value: object, tool: object) -> None:
        if isinstance(identity, str) and identity and identity not in self._completed_ids:
            self._pending_calls[identity] = nonempty(value, tool)

    def observe(self, event: object) -> bool:
        if not isinstance(event, dict):
            return self.exceeded
        part = event.get("part")
        if isinstance(part, dict):
            identity = part.get("callID", part.get("id"))
            state = part.get("state")
            state = state if isinstance(state, dict) else {}
            if event.get("type") == "tool_use":
                if state.get("status") in ("completed", "error"):
                    self._complete(identity, nonempty(state.get("input"), part.get("tool")))
                else:
                    self._pending(identity, state.get("input"), part.get("tool"))
            elif event.get("type") == "tool_result":
                self._complete(identity)
        if event.get("type") == "tool_result":
            self._complete(event.get("tool_use_id"))
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        for item in content if isinstance(content, list) else []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "tool_use":
                self._pending(item.get("id"), item.get("input"), item.get("name"))
            elif item.get("type") == "tool_result":
                self._complete(item.get("tool_use_id"))
        return self.exceeded


def check_logs(paths: list[Path], limit: int) -> int:
    # A restarted runtime has a new call-ID namespace; sum completed attempts.
    total = 0
    for path in paths:
        budget = ToolCallBudget(limit)
        with path.open() as stream:
            for line in stream:
                if line.strip():
                    budget.observe(json.loads(line))
        total += budget.count
    return total


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, action="append", required=True)
    parser.add_argument("--limit", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        ToolCallBudget(args.limit)
        total = check_logs(args.log, args.limit)
    except (OSError, ValueError):
        return 2
    print(json.dumps({"count": total, "limit": args.limit, "exceeded": total > args.limit}))
    return 75 if total > args.limit else 0


if __name__ == "__main__":
    raise SystemExit(main())
