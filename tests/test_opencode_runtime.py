import json

import pytest

from codex_local_harness.health import read_jsonl
from codex_local_harness.runtime.opencode import main, parse_log


def write_events(path):
    events = [
        {"type": "step_start", "timestamp": 1000, "part": {"type": "step-start"}},
        {
            "type": "tool_use",
            "timestamp": 1500,
            "part": {"tool": "read", "state": {"status": "completed", "input": "not retained"}},
        },
        {
            "type": "step_finish",
            "timestamp": 2000,
            "part": {"tokens": {"input": 100, "output": 20, "reasoning": 5, "total": 125}},
        },
        {
            "type": "tool_use",
            "timestamp": 2500,
            "part": {"tool": "bash", "state": {"status": "error", "output": "not retained"}},
        },
        {
            "type": "step_finish",
            "timestamp": 3000,
            "part": {"tokens": {"input": 120, "output": 10, "reasoning": 0, "total": 130}},
        },
        {"type": "text", "part": {"text": "prompt and response content are ignored"}},
    ]
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def test_parser_extracts_only_normalized_activity(tmp_path):
    path = tmp_path / "events.jsonl"
    write_events(path)
    stats = parse_log(path)
    assert stats.input_tokens == 220
    assert stats.output_tokens == 30
    assert stats.reasoning_tokens == 5
    assert stats.turns == 2
    assert stats.tool_calls == 2
    assert stats.tool_errors == 1
    assert stats.wall_seconds == 2.0
    assert "not retained" not in repr(stats)


def test_cli_appends_health_record_without_event_content(tmp_path, capsys):
    log = tmp_path / "events.jsonl"
    records = tmp_path / "health.jsonl"
    write_events(log)
    result = main(
        [
            "--log", str(log), "--records", str(records), "--run-id", "run-1",
            "--issue", "4", "--role", "helper", "--model", "qwen3.8-27b",
            "--profile", "codex-led-v4", "--task-class", "routine", "--status", "success",
            "--inference-status", "0", "--workflow-status", "0", "--delivery-ready", "true",
        ]
    )
    assert result == 0
    health = read_jsonl(records)[0]
    assert health.tool_errors == 1
    assert health.usage.local_total == 255
    assert health.delivery_ready is True
    assert "prompt and response" not in records.read_text()
    assert json.loads(capsys.readouterr().out)["turns"] == 2


def test_parser_reports_malformed_line(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{}\n{"type":"step_finish","part":{"tokens":{"input":-1}}}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 2"):
        parse_log(path)


def test_cli_can_record_malformed_result_without_claiming_token_usage(tmp_path):
    log = tmp_path / "events.jsonl"
    records = tmp_path / "health.jsonl"
    log.write_text("not-json\n", encoding="utf-8")
    assert main([
        "--log", str(log), "--records", str(records), "--run-id", "run-bad",
        "--issue", "53", "--role", "helper", "--model", "qwen3.8-27b",
        "--profile", "codex-led-v4", "--task-class", "routine", "--status", "failure",
        "--failure-class", "malformed-result", "--inference-status", "0",
        "--workflow-status", "65", "--allow-malformed", "--repository", "owner/repo",
    ]) == 0
    health = read_jsonl(records)[0]
    assert health.failure_class == "malformed-result"
    assert health.usage.local_total == 0
    assert health.repository == "owner/repo"


def test_parser_handles_cache_tokens(tmp_path):
    path = tmp_path / "events.jsonl"
    events = [
        {"type": "step_start", "timestamp": 1000},
        {
            "type": "step_finish",
            "timestamp": 2000,
            "part": {"tokens": {"input": 100, "output": 20, "reasoning": 5, "cache": {"read": 50, "write": 25}}},
        },
    ]
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    stats = parse_log(path)
    assert stats.input_tokens == 100
    assert stats.cache_read == 50
    assert stats.cache_write == 25
    assert stats.cache_provenance == "observed"


def test_parser_handles_cache_tokens_zero_values(tmp_path):
    path = tmp_path / "events.jsonl"
    events = [
        {
            "type": "step_finish",
            "timestamp": 2000,
            "part": {"tokens": {"input": 100, "cache": {"read": 0, "write": 0}}},
        },
    ]
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    stats = parse_log(path)
    assert stats.cache_read == 0
    assert stats.cache_write == 0
    assert stats.cache_provenance == "observed"


def test_parser_rejects_malformed_cache(tmp_path):
    path = tmp_path / "events.jsonl"
    events = [
        {
            "type": "step_finish",
            "timestamp": 2000,
            "part": {"tokens": {"input": 100, "cache": "not-a-dict"}},
        },
    ]
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid cache object"):
        parse_log(path)


def test_parser_rejects_negative_cache_values(tmp_path):
    path = tmp_path / "events.jsonl"
    events = [
        {
            "type": "step_finish",
            "timestamp": 2000,
            "part": {"tokens": {"input": 100, "cache": {"read": -1, "write": 0}}},
        },
    ]
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    with pytest.raises(ValueError, match="cache read"):
        parse_log(path)


def test_parser_defaults_unsupported_when_no_cache_in_log(tmp_path):
    path = tmp_path / "events.jsonl"
    events = [
        {
            "type": "step_finish",
            "timestamp": 2000,
            "part": {"tokens": {"input": 100, "output": 20}},
        },
    ]
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    stats = parse_log(path)
    assert stats.cache_read is None
    assert stats.cache_write is None
    assert stats.cache_provenance == "unsupported"  # Default when no cache object
