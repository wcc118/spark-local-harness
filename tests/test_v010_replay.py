from pathlib import Path

from codex_local_harness.dashboard import DashboardSource, render_dashboard


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "qualification" / "v010"


def test_v010_replay_explains_all_four_states():
    snapshot = DashboardSource(
        FIXTURES / "replay-runs.jsonl",
        FIXTURES / "replay-adjudications.jsonl",
        FIXTURES / "replay-lifecycle.jsonl",
    ).snapshot()
    summary = snapshot["summary"]
    assert summary["runs"] == 4
    assert summary["adjudicated_runs"] == 4
    assert summary["unadjudicated_runs"] == 0
    assert summary["contradictions"] == 1
    assert summary["interrupted_runs"] == 1
    assert summary["effective_outcome_counts"] == {"failed": 1, "interrupted": 1, "passed": 2}
    assert summary["confirmed_local_autonomy_rate"] == 0.5

    by_id = {run["run_id"]: run for run in snapshot["runs"]}
    hidden = by_id["20260815T164648Z-402544"]
    assert hidden["claimed_status"] == "success"
    assert hidden["effective_outcome"] == "failed"
    assert hidden["contradiction"] is True
    correction = by_id["20260815T165005Z-408263"]
    assert correction["claimed_status"] is None
    assert correction["correction_of"] == "20260815T164648Z-402544"
    assert correction["lifecycle_dirty_entries"] == 1
    assert correction["issue"] == 19
    assert correction["role"] == "coder"
    assert correction["task_class"] == "difficult-concurrency-correction"

    page = render_dashboard(snapshot)
    for label in ("shadow-coder-repeat", "moderate-identity", "unclassified", "difficult-concurrency-correction", "interrupted"):
        assert label in page
