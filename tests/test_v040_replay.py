from pathlib import Path

from codex_local_harness.plan_event import derive_plans, read_plan_records, validate_append


ROOT = Path(__file__).resolve().parents[1]


def test_v040_plan_replay_preserves_revision_history_and_current_step():
    events = read_plan_records(ROOT / "qualification/v040/replay-plans.jsonl")
    validate_append([], events[0])
    validate_append(events[:1], events[1])
    plan = derive_plans(events)[0]
    assert (plan["revision"], plan["completed_steps"], plan["total_steps"]) == (2, 2, 3)
    assert plan["current_step"]["step_id"] == "smoke"
    assert [item["state"] for item in plan["history"]] == ["superseded", "current"]
    assert plan["revision_reason"] == "Dashboard tests passed; execution advanced to live smoke"
