from __future__ import annotations

import argparse
import json
import re
import uuid
from pathlib import Path

from .diagnostics import append_record
from .operation_usage import PHASES, OperationUsageSnapshot

# Privacy boundary: do not decode unrelated event lines.
ASSISTANT_DISCRIMINATOR = re.compile(r'"type"\s*:\s*"assistant"')


def find_transcript(session_root: Path, session_id: str) -> Path:
    try:
        parsed = uuid.UUID(session_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("session id must be a UUID") from exc
    if str(parsed) != session_id.lower():
        raise ValueError("session id must be a UUID")
    path = session_root / f"{session_id}.jsonl"
    if not path.is_file():
        raise ValueError(f"no transcript found for session {session_id} under {session_root}")
    return path


def read_cumulative_usage(path: Path) -> tuple[str, dict[str, int]]:
    """Sum every assistant turn's usage in a Claude Code transcript.

    Claude's per-message `usage` object is a per-turn reading, not a running
    total the way Codex's `token_count` event is. Summing the whole file
    produces an equivalent cumulative reading as of the newest turn present;
    capturing this at operation start and finish and diffing (already done
    by operation_usage.derive_usage) yields the operation's token delta,
    same as the Codex path.
    """
    totals = {
        "input_tokens": 0, "cached_input_tokens": 0, "cache_write_input_tokens": 0,
        "output_tokens": 0, "reasoning_output_tokens": 0, "total_tokens": 0,
    }
    latest_observed_at: str | None = None
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not ASSISTANT_DISCRIMINATOR.search(line):
                continue
            event = json.loads(line)
            if event.get("type") != "assistant":
                continue
            observed_at = event.get("timestamp")
            if not isinstance(observed_at, str):
                continue
            message = event.get("message")
            usage = message.get("usage") if isinstance(message, dict) else None
            if not isinstance(usage, dict):
                # Not every assistant record carries its own usage block
                # (e.g. tool-only continuations); skip rather than fail.
                continue
            input_tokens = usage.get("input_tokens")
            cache_read = usage.get("cache_read_input_tokens")
            cache_write = usage.get("cache_creation_input_tokens")
            output_tokens = usage.get("output_tokens")
            counters = (input_tokens, cache_read, cache_write, output_tokens)
            if not all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in counters):
                raise ValueError("invalid usage counters in assistant event")
            # Claude Code does not report reasoning tokens as a separate
            # billed counter the way Codex does; output_tokens_details
            # .thinking_tokens is a breakdown of output_tokens, not an
            # addition to it, so reasoning_output_tokens stays 0 here to
            # avoid double-counting rather than guessing at a split.
            totals["input_tokens"] += input_tokens
            totals["cached_input_tokens"] += cache_read
            totals["cache_write_input_tokens"] += cache_write
            totals["output_tokens"] += output_tokens
            totals["total_tokens"] += input_tokens + cache_read + cache_write + output_tokens
            latest_observed_at = observed_at
    if latest_observed_at is None:
        raise ValueError("no assistant usage events found in transcript")
    return latest_observed_at, totals


def capture_snapshot(
    *, session_root: Path, session_id: str, phase: str, repository: str,
    issue: int, operation_id: str, record_id: str, context_window: int,
) -> OperationUsageSnapshot:
    observed_at, totals = read_cumulative_usage(find_transcript(session_root, session_id))
    return OperationUsageSnapshot(
        record_id=record_id, observed_at=observed_at, phase=phase,
        session_id=session_id, repository=repository, issue=issue,
        operation_id=operation_id, source="claude-transcript-usage",
        context_window=context_window, **totals,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture privacy-minimal Claude Code operation token counters")
    parser.add_argument("phase", choices=sorted(PHASES))
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--session-root", type=Path, required=True, help="Directory holding <session-id>.jsonl transcripts, e.g. ~/.claude/projects/<mangled-cwd>")
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--record-id", required=True)
    parser.add_argument("--context-window", type=int, required=True, help="Claude has no scriptable context-window interface; pass the vendor-published value for the model in use")
    args = parser.parse_args(argv)
    try:
        record = capture_snapshot(
            session_root=args.session_root, session_id=args.session_id, phase=args.phase,
            repository=args.repository, issue=args.issue, operation_id=args.operation_id,
            record_id=args.record_id, context_window=args.context_window,
        )
        append_record(args.records, record)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(record.as_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
