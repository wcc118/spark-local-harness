import json

import pytest

from codex_local_harness.diagnostics import Adjudication, append_record
from codex_local_harness.health import RunHealth, TokenUsage, append_jsonl, summarize
from codex_local_harness.monitor import HealthPolicy, assess, main, report


def run_health(index, **changes):
    values = {
        "run_id": f"run-{index}",
        "issue": index + 1,
        "role": "coder",
        "runtime": "opencode",
        "model": "qwen3-coder-next",
        "profile": "codex-led-v4",
        "task_class": "routine",
        "status": "success",
        "wall_seconds": 10,
        "usage": TokenUsage(local_input=100),
        "outcome": "accepted",
    }
    values.update(changes)
    return RunHealth(**values)


def test_assessment_waits_for_minimum_sample():
    result = assess(summarize([run_health(0)]), HealthPolicy(minimum_runs=2))
    assert result["status"] == "insufficient-data"
    assert result["reasons"] == ["minimum-runs:1/2"]


def test_healthy_local_first_window_is_green():
    records = [run_health(index) for index in range(10)]
    result = assess(summarize(records))
    assert result["status"] == "green"
    assert result["reasons"] == []


def test_frontier_and_infrastructure_regressions_are_red():
    records = [
        run_health(
            index,
            intervention_depth="takeover",
            usage=TokenUsage(local_input=10, frontier_input=100),
            failure_class="infrastructure-endpoint" if index < 3 else None,
            correction_cycle=2,
        )
        for index in range(10)
    ]
    result = assess(summarize(records))
    assert result["status"] == "red"
    assert any(reason.startswith("frontier-token-share:critical") for reason in result["reasons"])
    assert any(reason.startswith("infrastructure-failure-rate:critical") for reason in result["reasons"])


def test_report_reads_jsonl_and_cli_emits_json(tmp_path, capsys):
    path = tmp_path / "runs.jsonl"
    for index in range(2):
        append_jsonl(path, run_health(index))
    result = report(path, HealthPolicy(minimum_runs=2))
    assert result["assessment"]["status"] == "green"
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps({"minimum_runs": 2}), encoding="utf-8")
    assert main([str(path), "--policy", str(policy_path)]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["runs"] == 2


def test_report_exposes_adjudicated_truth_without_rewriting_health(tmp_path):
    records = tmp_path / "runs.jsonl"
    adjudications = tmp_path / "adjudications.jsonl"
    append_jsonl(records, run_health(0))
    original = records.read_bytes()
    append_record(adjudications, Adjudication(
        adjudication_id="adj-1", run_id="run-0", verdict="failed", source="evaluator",
        observed_at="2026-08-15T17:00:00Z", failure_class="test-hidden",
    ))
    result = report(records, adjudications_path=adjudications)
    assert result["worker_claims"]["summary"]["status_counts"] == {"success": 1}
    assert result["summary"]["effective_outcome_counts"] == {"failed": 1}
    assert result["summary"]["contradictions"] == 1
    assert result["assessment"]["status"] == "insufficient-data"
    assert result["assessment"]["reasons"] == ["minimum-adjudicated-runs:1/10"]
    assert records.read_bytes() == original


def test_policy_rejects_inverted_thresholds():
    with pytest.raises(ValueError, match="frontier warning"):
        HealthPolicy(frontier_share_warning=0.3, frontier_share_critical=0.2)
