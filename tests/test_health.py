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
