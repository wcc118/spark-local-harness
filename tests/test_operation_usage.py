from __future__ import annotations

import json

import pytest

from codex_local_harness.operation_usage import (
    OperationUsageSnapshot, capture_snapshot, derive_usage, find_rollout,
    read_latest_token_count,
)


THREAD = "01a002fc-d275-7a92-b3da-840ebb61a002"


def token_event(timestamp: str, **changes):
    last = changes.pop("last_token_usage", None)
    usage = {
        "input_tokens": 100, "cached_input_tokens": 60,
        "cache_write_input_tokens": 0, "output_tokens": 20,
        "reasoning_output_tokens": 5, "total_tokens": 120,
    }
    usage.update(changes)
    return {
        "timestamp": timestamp, "type": "event_msg",
        "payload": {"type": "token_count", "info": {
            "total_token_usage": usage, "model_context_window": 258400,
            **({"last_token_usage": last} if last is not None else {}),
        }},
    }


def snapshot(identifier: str, phase: str, observed: str, **changes):
    values = {
        "input_tokens": 100, "cached_input_tokens": 60,
        "cache_write_input_tokens": 0, "output_tokens": 20,
        "reasoning_output_tokens": 5, "total_tokens": 120,
        "context_window": 258400, "last_input_tokens": None,
    }
    values.update(changes)
    return OperationUsageSnapshot(
        identifier, observed, phase, THREAD, "owner/repo", 62, "issue-62",
        "codex-rollout-token-count", **values,
    )


def test_rollout_reader_ignores_content_lines_before_decoding(tmp_path):
    path = tmp_path / f"rollout-now-{THREAD}.jsonl"
    secret = "DO-NOT-RETAIN-PROMPT-CONTENT"
    path.write_text(
        json.dumps({"type": "response_item", "payload": {"content": secret}}) + "\n" +
        json.dumps(token_event("2026-08-16T01:00:00Z")) + "\n" +
        json.dumps(token_event("2026-08-16T01:01:00Z", input_tokens=140), separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    observed_at, values = read_latest_token_count(path)
    assert observed_at == "2026-08-16T01:01:00Z"
    assert values["input_tokens"] == 140
    assert secret not in repr(values)


def test_capture_requires_exact_thread_filename(tmp_path):
    nested = tmp_path / "2026/08/16"
    nested.mkdir(parents=True)
    rollout = nested / f"rollout-now-{THREAD}.jsonl"
    rollout.write_text(json.dumps(token_event("2026-08-16T01:00:00Z"), separators=(",", ":")) + "\n")
    assert find_rollout(tmp_path, THREAD) == rollout
    record = capture_snapshot(
        session_root=tmp_path, thread_id=THREAD, phase="start", repository="owner/repo",
        issue=62, operation_id="issue-62", record_id="start-62",
    )
    assert record.session_id == THREAD
    assert record.input_tokens == 100


def test_usage_delta_preserves_uncached_cached_generated_and_reasoning():
    start = snapshot("start", "start", "2026-08-16T01:00:00Z")
    finish = snapshot(
        "finish", "finish", "2026-08-16T01:10:00Z", input_tokens=300,
        cached_input_tokens=180, cache_write_input_tokens=10,
        output_tokens=50, reasoning_output_tokens=15, total_tokens=350,
    )
    usage = derive_usage([start, finish])[0]
    assert usage["status"] == "observed"
    assert usage["input_tokens"] == 200
    assert usage["cached_input_tokens"] == 120
    assert usage["uncached_input_tokens"] == 80
    assert usage["cache_write_input_tokens"] == 10
    assert usage["output_tokens"] == 30
    assert usage["reasoning_output_tokens"] == 10


def test_missing_finish_and_counter_reset_are_unavailable_not_zero():
    start = snapshot("start", "start", "2026-08-16T01:00:00Z")
    assert derive_usage([start])[0]["diagnostic"] == "missing-finish"
    finish = snapshot(
        "finish", "finish", "2026-08-16T01:10:00Z", input_tokens=10,
        cached_input_tokens=5, output_tokens=2, reasoning_output_tokens=1, total_tokens=12,
    )
    reset = derive_usage([start, finish])[0]
    assert reset["status"] == "unavailable"
    assert reset["diagnostic"] == "counter-reset"
    assert "input_tokens" not in reset


def test_duplicate_or_unsafe_rollout_identity_is_rejected(tmp_path):
    for index in (1, 2):
        (tmp_path / f"rollout-{index}-{THREAD}.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one"):
        find_rollout(tmp_path, THREAD)
    with pytest.raises(ValueError, match="UUID"):
        find_rollout(tmp_path, "../../unsafe")


def test_last_token_usage_availability_and_malformed_fields(tmp_path):
    path = tmp_path / f"rollout-later-{THREAD}.jsonl"
    path.write_text(
        json.dumps(
            {
                "timestamp": "2026-09-06T12:00:00Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 200,
                            "cached_input_tokens": 120,
                            "cache_write_input_tokens": 10,
                            "output_tokens": 30,
                            "reasoning_output_tokens": 10,
                            "total_tokens": 240,
                        },
                        "model_context_window": 258400,
                    },
                },
            },
            separators=(",", ":"),
        )
        + "\n"
        + json.dumps(
            {
                "timestamp": "2026-09-06T12:01:00Z",
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "info": {
                        "total_token_usage": {
                            "input_tokens": 220,
                            "cached_input_tokens": 130,
                            "cache_write_input_tokens": 15,
                            "output_tokens": 35,
                            "reasoning_output_tokens": 12,
                            "total_tokens": 260,
                        },
                        "model_context_window": 258400,
                        "last_token_usage": {
                            "input_tokens": 22000,
                            "cached_input_tokens": 18000,
                            "cache_write_input_tokens": 0,
                            "output_tokens": 500,
                            "reasoning_output_tokens": 200,
                            "total_tokens": 22700,
                        },
                    },
                },
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    observed_at, values = read_latest_token_count(path)
    assert observed_at == "2026-09-06T12:01:00Z"
    assert values["last_input_tokens"] == 22000
    assert values["last_cached_input_tokens"] == 18000
    assert values["context_window"] == 258400
    # last_input_tokens absent in first event should be None
    first_path = tmp_path / f"rollout-earlier-{THREAD}.jsonl"
    first_path.write_text(
        json.dumps(token_event("2026-09-06T11:00:00Z"), separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    first_at, first_vals = read_latest_token_count(first_path)
    assert first_at == "2026-09-06T11:00:00Z"
    assert first_vals["last_input_tokens"] is None


def test_usage_delta_computes_last_input_pressure_and_headroom():
    start = snapshot("start", "start", "2026-08-16T01:00:00Z")
    finish = snapshot(
        "finish", "finish", "2026-08-16T01:10:00Z", input_tokens=120000,
        cached_input_tokens=72000, cache_write_input_tokens=5000,
        output_tokens=15000, reasoning_output_tokens=5000, total_tokens=130000,
        last_input_tokens=22000,
    )
    usage = derive_usage([start, finish])[0]
    assert usage["status"] == "observed"
    assert usage["last_input_tokens"] == 22000
    assert usage["context_pressure"] == pytest.approx(22000 / 258400)
    assert usage["context_headroom"] == pytest.approx(1.0 - (22000 / 258400))


@pytest.mark.parametrize("invalid", [True, -1, "22000"])
def test_malformed_last_token_usage_is_rejected(tmp_path, invalid):
    path = tmp_path / f"rollout-invalid-{THREAD}.jsonl"
    event = token_event(
        "2026-09-06T12:00:00Z",
        last_token_usage={
            "input_tokens": invalid,
            "cached_input_tokens": 10,
            "cache_write_input_tokens": 0,
            "output_tokens": 1,
            "reasoning_output_tokens": 0,
            "total_tokens": 11,
        },
    )
    path.write_text(json.dumps(event, separators=(",", ":")) + "\n")
    with pytest.raises(ValueError, match="invalid input_tokens in last_token_usage"):
        read_latest_token_count(path)
