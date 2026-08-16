import json

import pytest

from codex_local_harness.health import RunHealth, TokenUsage, append_jsonl, read_jsonl, summarize


def record(**changes):
    values = {
        "run_id": "run-1",
        "issue": 7,
        "role": "coder",
        "runtime": "opencode",
        "model": "qwen3-coder-next",
        "profile": "codex-led-v4",
        "task_class": "moderate",
        "status": "success",
        "wall_seconds": 12.5,
        "usage": TokenUsage(local_input=100, local_output=20),
    }
    values.update(changes)
    return RunHealth(**values)


def test_health_round_trip_and_derived_totals(tmp_path):
    path = tmp_path / "health.jsonl"
    append_jsonl(path, record())
    loaded = read_jsonl(path)
    assert loaded == (record(),)
    payload = json.loads(path.read_text())
    assert payload["usage"]["local_total"] == 120
    assert "prompt" not in payload


def test_summary_separates_local_and_frontier_usage():
    records = [
        record(),
        record(
            run_id="run-2",
            intervention_depth="targeted",
            correction_cycle=1,
            usage=TokenUsage(local_input=80, frontier_input=10, frontier_output=5),
            scout_findings=2,
            useful_scout_findings=1,
        ),
    ]
    result = summarize(records)
    assert result["local_tokens"] == 200
    assert result["frontier_tokens"] == 15
    assert result["frontier_token_share"] == pytest.approx(15 / 215)
    assert result["local_autonomy_rate"] == 0.5
    assert result["codex_targeted_runs"] == 1
    assert result["correction_cycles"] == 1


def test_summary_distinguishes_delivery_readiness_from_inference_success():
    records = [
        record(delivery_ready=True, inference_status=0, workflow_status=0),
        record(run_id="run-2", status="degraded", delivery_ready=False, inference_status=0, workflow_status=0, dirty_entries=1, generated_artifacts=1),
    ]
    result = summarize(records)
    assert result["delivery_ready_rate"] == 0.5
    assert result["delivery_observations"] == 2
    assert result["dirty_entries"] == 1
    assert result["generated_artifacts"] == 1


def test_health_rejects_impossible_values():
    with pytest.raises(ValueError, match="non-negative"):
        TokenUsage(local_input=-1)
    with pytest.raises(ValueError, match="useful findings"):
        record(scout_findings=0, useful_scout_findings=1)
    with pytest.raises(ValueError, match="intervention"):
        record(intervention_depth="sometimes")


def test_invalid_jsonl_reports_line(tmp_path):
    path = tmp_path / "health.jsonl"
    path.write_text("{}\nnot-json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
        read_jsonl(path)


def test_run_publication_is_idempotent_and_rejects_conflicting_identity(tmp_path):
    path = tmp_path / "health.jsonl"
    assert append_jsonl(path, record(repository="owner/repo", pull_request=12)) is True
    original = path.read_bytes()
    assert append_jsonl(path, record(repository="owner/repo", pull_request=12)) is False
    assert path.read_bytes() == original
    with pytest.raises(ValueError, match="conflicting health record"):
        append_jsonl(path, record(status="failed"))


def test_token_usage_default_cache_values():
    usage = TokenUsage()
    assert usage.local_cache_read is None
    assert usage.local_cache_write is None
    assert usage.cache_provenance is None


def test_token_usage_with_observed_cache():
    usage = TokenUsage(local_cache_read=10, local_cache_write=5, cache_provenance="observed")
    assert usage.local_cache_read == 10
    assert usage.local_cache_write == 5
    assert usage.cache_provenance == "observed"


def test_token_usage_with_zero_cache_values():
    # Explicitly zero cache should be allowed
    usage = TokenUsage(local_cache_read=0, local_cache_write=0, cache_provenance="observed")
    assert usage.local_cache_read == 0
    assert usage.local_cache_write == 0


def test_token_usage_rejects_invalid_provenance():
    with pytest.raises(ValueError, match="cache_provenance"):
        TokenUsage(cache_provenance="invalid")


def test_token_usage_backward_compatibility_with_runhealth_from_dict():
    # When creating a TokenUsage directly, cache_provenance is None by default
    # The "unsupported" default only applies in RunHealth.from_dict for backward compatibility
    old_data = {
        "local_input": 100,
        "local_output": 20,
    }
    usage = TokenUsage(**old_data)
    assert usage.local_cache_read is None
    assert usage.local_cache_write is None
    assert usage.cache_provenance is None  # Direct instantiation has None by default


def test_runhealth_from_dict_defaults_unsupported_for_legacy_records():
    # When deserializing from JSON (old records without cache fields),
    # cache_provenance should default to "unsupported" for backward compatibility
    old_health_data = {
        "run_id": "run-1",
        "issue": 7,
        "role": "coder",
        "runtime": "opencode",
        "model": "qwen3-coder-next",
        "profile": "codex-led-v4",
        "task_class": "moderate",
        "status": "success",
        "wall_seconds": 12.5,
        "usage": {
            "local_input": 100,
            "local_output": 20,
        },
    }
    health = RunHealth.from_dict(old_health_data)
    assert health.usage.local_cache_read is None
    assert health.usage.local_cache_write is None
    assert health.usage.cache_provenance == "unsupported"  # Default for legacy records


def test_run_health_from_dict_with_new_cache_fields():
    data = {
        "run_id": "run-1",
        "issue": 7,
        "role": "coder",
        "runtime": "opencode",
        "model": "qwen3-coder-next",
        "profile": "codex-led-v4",
        "task_class": "moderate",
        "status": "success",
        "wall_seconds": 12.5,
        "usage": {
            "local_input": 100,
            "local_output": 20,
            "local_cache_read": 10,
            "local_cache_write": 5,
            "cache_provenance": "observed",
        },
    }
    health = RunHealth.from_dict(data)
    assert health.usage.local_cache_read == 10
    assert health.usage.local_cache_write == 5
    assert health.usage.cache_provenance == "observed"


def test_run_health_from_dict_backward_compatibility():
    # Old records without cache fields in usage should deserialize with defaults
    data = {
        "run_id": "run-1",
        "issue": 7,
        "role": "coder",
        "runtime": "opencode",
        "model": "qwen3-coder-next",
        "profile": "codex-led-v4",
        "task_class": "moderate",
        "status": "success",
        "wall_seconds": 12.5,
        "usage": {
            "local_input": 100,
            "local_output": 20,
        },
    }
    health = RunHealth.from_dict(data)
    # Legacy records should have cache_provenance default to "unsupported"
    assert health.usage.local_cache_read is None
    assert health.usage.local_cache_write is None
    assert health.usage.cache_provenance == "unsupported"
