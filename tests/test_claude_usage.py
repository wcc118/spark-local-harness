from __future__ import annotations

import json

import pytest

from codex_local_harness.claude_usage import capture_snapshot, find_transcript, read_cumulative_usage
from codex_local_harness.operation_usage import derive_usage


SESSION = "01a002fc-d275-7a92-b3da-840ebb61a002"


def assistant_turn(timestamp: str, **changes):
    usage = {
        "input_tokens": 10, "cache_read_input_tokens": 5,
        "cache_creation_input_tokens": 0, "output_tokens": 4,
        "output_tokens_details": {"thinking_tokens": 1},
    }
    usage.update(changes)
    return {"type": "assistant", "timestamp": timestamp, "message": {"usage": usage}}


def test_transcript_reader_ignores_unrelated_lines_before_decoding(tmp_path):
    path = tmp_path / f"{SESSION}.jsonl"
    path.write_text(
        '{"type": "user", "message": {"content": "not valid json here -->'  # deliberately malformed, never parsed
        + "\n" +
        json.dumps(assistant_turn("2026-08-16T01:00:00Z")) + "\n" +
        json.dumps(assistant_turn("2026-08-16T01:01:00Z", input_tokens=30), separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    observed_at, totals = read_cumulative_usage(path)
    assert observed_at == "2026-08-16T01:01:00Z"
    assert totals["input_tokens"] == 40  # 10 + 30, summed across both turns
    assert totals["cached_input_tokens"] == 10  # cache_read summed across both turns
    assert totals["reasoning_output_tokens"] == 0  # folded into output_tokens, not double-counted
    assert totals["total_tokens"] == totals["input_tokens"] + totals["cached_input_tokens"] + totals["output_tokens"]


def test_find_transcript_requires_a_uuid_session_id(tmp_path):
    (tmp_path / f"{SESSION}.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="UUID"):
        find_transcript(tmp_path, "../../unsafe")
    with pytest.raises(ValueError, match="no transcript found"):
        find_transcript(tmp_path, "11111111-1111-1111-1111-111111111111")
    assert find_transcript(tmp_path, SESSION) == tmp_path / f"{SESSION}.jsonl"


def test_capture_snapshot_tags_source_and_carries_operator_supplied_context_window(tmp_path):
    path = tmp_path / f"{SESSION}.jsonl"
    path.write_text(json.dumps(assistant_turn("2026-08-16T01:00:00Z")) + "\n", encoding="utf-8")
    record = capture_snapshot(
        session_root=tmp_path, session_id=SESSION, phase="start", repository="owner/repo",
        issue=62, operation_id="issue-62", record_id="start-62", context_window=200000,
    )
    assert record.source == "claude-transcript-usage"
    assert record.context_window == 200000
    assert record.input_tokens == 10
    assert record.cached_input_tokens == 5


def test_start_finish_cumulative_sums_diff_to_the_operation_window(tmp_path):
    path = tmp_path / f"{SESSION}.jsonl"
    path.write_text(json.dumps(assistant_turn("2026-08-16T01:00:00Z")) + "\n", encoding="utf-8")
    start = capture_snapshot(
        session_root=tmp_path, session_id=SESSION, phase="start", repository="owner/repo",
        issue=62, operation_id="issue-62", record_id="start-62", context_window=200000,
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(assistant_turn("2026-08-16T01:10:00Z", input_tokens=50, output_tokens=20)) + "\n")
    finish = capture_snapshot(
        session_root=tmp_path, session_id=SESSION, phase="finish", repository="owner/repo",
        issue=62, operation_id="issue-62", record_id="finish-62", context_window=200000,
    )
    usage = derive_usage([start, finish])[0]
    assert usage["status"] == "observed"
    assert usage["input_tokens"] == 50  # only the second turn happened during the operation window
    assert usage["output_tokens"] == 20


def test_invalid_usage_counters_are_rejected_not_coerced(tmp_path):
    path = tmp_path / f"{SESSION}.jsonl"
    path.write_text(json.dumps(assistant_turn("2026-08-16T01:00:00Z", input_tokens=-1)) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid usage counters"):
        read_cumulative_usage(path)


def test_transcript_with_no_assistant_usage_is_an_explicit_error(tmp_path):
    path = tmp_path / f"{SESSION}.jsonl"
    path.write_text(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no assistant usage events"):
        read_cumulative_usage(path)
