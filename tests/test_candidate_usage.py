import json

import pytest

from codex_local_harness.usage import normalize, record


def write(path, events):
    path.write_text("".join(json.dumps(e) + "\n" for e in events))


def test_qwen_terminal_is_not_added_to_message_usage_and_errors_are_deduplicated(tmp_path):
    log = tmp_path / "qwen.jsonl"
    usage = {"input_tokens": 494079, "output_tokens": 11061, "total_tokens": 505140, "cache_read_input_tokens": 0}
    error = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "is_error": True}]}}
    write(log, [{"type": "assistant", "message": {"id": "m", "usage": usage}}, error, error,
                {"type": "result", "usage": usage}])
    data = normalize(log, "qwen-code", 0)
    assert data["total_including_cache"] == 505140
    assert data["coverage"] == "complete"
    assert data["tool_errors"] == 1
    ledger = tmp_path / "usage.jsonl"
    record(ledger, "run", data)
    record(ledger, "run", data)
    assert len(ledger.read_text().splitlines()) == 1
    with pytest.raises(ValueError, match="Conflicting"):
        record(ledger, "run", data | {"coverage": "partial"})


def test_interrupted_message_usage_is_partial_and_empty_is_unknown(tmp_path):
    log = tmp_path / "qwen.jsonl"
    write(log, [{"type": "assistant", "message": {"id": "stub", "usage": {"input_tokens": 0, "output_tokens": 0}}},
                {"type": "assistant", "message": {"id": "m", "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}}}])
    data = normalize(log, "qwen-code", 137)
    assert data["coverage"] == "partial"
    assert data["total_including_cache"] == 12
    log.write_text("")
    data = normalize(log, "opencode", 137)
    assert data["coverage"] == "unknown"
    assert data["total_including_cache"] is None


def test_cache_views_use_provider_total_without_adding_reasoning_again(tmp_path):
    log = tmp_path / "deepseek.jsonl"
    write(log, [{"type": "step_finish", "part": {"id": "a", "tokens": {
        "input": 32218, "output": 17654, "reasoning": 100,
        "cache": {"read": 32768, "write": 0}, "total": 82640}}}])
    data = normalize(log, "opencode", 0)
    assert data["total_including_cache"] == 82640
    assert data["total_excluding_cache"] == 49872
    assert data["reasoning_reported"] == 100
