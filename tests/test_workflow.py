from codex_local_harness.workflow import WorkflowEvent, derive_operations


def event(identifier, stage, state, observed_at, **changes):
    values = {"event_id": identifier, "operation_id": "issue-37", "stage": stage, "state": state, "observed_at": observed_at, "issue": 37}
    values.update(changes)
    return WorkflowEvent(**values)


def test_workflow_derives_current_position_and_stage_history():
    operations = derive_operations([
        event("one", "intake", "passed", "2026-08-15T18:00:00Z"),
        event("two", "routing", "passed", "2026-08-15T18:01:00Z"),
        event("three", "local-work", "active", "2026-08-15T18:02:00Z", run_id="run-37", role="coder", model="qwen"),
    ])
    operation = operations[0]
    assert (operation["current_stage"], operation["current_state"], operation["run_id"]) == ("local-work", "active", "run-37")
    assert [stage["state"] for stage in operation["stages"][:4]] == ["passed", "passed", "active", "pending"]


def test_workflow_preserves_correction_stop_reason():
    operation = derive_operations([
        event("one", "local-work", "passed", "2026-08-15T18:00:00Z"),
        event("two", "review", "failed", "2026-08-15T18:01:00Z"),
        event("three", "correction", "interrupted", "2026-08-15T18:02:00Z", correction_cycle=1, stop_reason="elapsed-budget"),
    ])[0]
    assert operation["current_stage"] == "correction"
    assert operation["stop_reason"] == "elapsed-budget"


def test_workflow_accepts_not_applicable_without_changing_pending_defaults():
    operation = derive_operations([
        event("one", "evidence", "passed", "2026-08-15T18:00:00Z"),
        event("two", "correction", "not-applicable", "2026-08-15T18:01:00Z"),
    ])[0]
    states = {stage["stage"]: stage["state"] for stage in operation["stages"]}
    assert states["correction"] == "not-applicable"
    assert states["merge"] == "pending"
