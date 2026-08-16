from pathlib import Path

from codex_local_harness.workflow import derive_operations, read_workflow_records


ROOT = Path(__file__).resolve().parents[1]


def test_v030_replay_preserves_evidence_first_unknowns_and_correction_loop():
    operations = {
        item["operation_id"]: item
        for item in derive_operations(read_workflow_records(ROOT / "qualification/v030/replay-workflow.jsonl"))
    }
    evidence_first = operations["issue-35-evidence-first"]
    assert all(item["state"] == "pending" for item in evidence_first["milestones"])
    assert evidence_first["activities"][0]["activity_type"] == "evidence"

    ordinary = operations["ordinary-flow"]
    assert [item["state"] for item in ordinary["milestones"]] == ["passed"] * 8
    assert not any(item["kind"] == "github-label-divergence" for item in ordinary["diagnostics"])

    correction = operations["correction-loop"]
    assert correction["current_milestone"] == "implementation"
    assert correction["correction_cycle"] == 1
    assert correction["activities"][-1]["activity_type"] == "correction"


def test_v020_ledger_is_unchanged_and_replayable_by_v030_projection():
    operations = derive_operations(read_workflow_records(ROOT / "qualification/v011/replay-workflow.jsonl"))
    assert operations
    assert all(item["projection_version"] == 2 for item in operations)
