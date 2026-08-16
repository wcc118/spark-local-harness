from codex_local_harness.workflow import WorkflowEvent, derive_operations


def event(identifier, stage, state, observed_at, **changes):
    values = {"event_id": identifier, "operation_id": "issue-37", "stage": stage, "state": state, "observed_at": observed_at, "issue": 37}
    values.update(changes)
    return WorkflowEvent(**values)


def test_workflow_event_accepts_session_id_and_repository():
    ev = event("one", "intake", "passed", "2026-08-15T18:00:00Z", session_id="session-123", repository="owner/repo")
    assert ev.session_id == "session-123"
    assert ev.repository == "owner/repo"


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


def test_workflow_grouping_by_session_and_repository():
    # Events with different session_id and repository should be in separate groups
    events = [
        event("one", "intake", "passed", "2026-08-15T18:00:00Z", operation_id="op-1", session_id="s1", repository="o1/r1"),
        event("two", "intake", "passed", "2026-08-15T18:01:00Z", operation_id="op-1", session_id="s2", repository="o1/r1"),
        event("three", "intake", "passed", "2026-08-15T18:02:00Z", operation_id="op-1", session_id="s1", repository="o1/r1"),
        event("four", "intake", "passed", "2026-08-15T18:03:00Z", operation_id="op-1", session_id="s1", repository="o2/r2"),
        # Event with both None should use "legacy" grouping
        event("five", "intake", "passed", "2026-08-15T18:04:00Z", operation_id="op-1"),
    ]
    operations = derive_operations(events)
    # Should have 4 groups: (s1, o1/r1), (s2, o1/r1), (s1, o2/r2), (legacy, legacy)
    assert len(operations) == 4
    session_repo_pairs = {(op["session_id"], op["repository"]) for op in operations}
    assert session_repo_pairs == {("s1", "o1/r1"), ("s2", "o1/r1"), ("s1", "o2/r2"), (None, None)}


def test_workflow_session_id_validation():
    from pytest import raises
    from codex_local_harness.workflow import WorkflowEvent
    # Valid session_id
    ev = WorkflowEvent(
        event_id="e1", operation_id="op1", stage="intake", state="passed",
        observed_at="2026-08-15T18:00:00Z", issue=1, session_id="valid-session"
    )
    assert ev.session_id == "valid-session"
    # Invalid session_id with spaces should fail
    with raises(ValueError):
        WorkflowEvent(
            event_id="e2", operation_id="op1", stage="intake", state="passed",
            observed_at="2026-08-15T18:00:00Z", issue=1, session_id="invalid session"
        )


def test_workflow_repository_validation():
    from pytest import raises
    from codex_local_harness.workflow import WorkflowEvent
    # Valid repository
    ev = WorkflowEvent(
        event_id="e1", operation_id="op1", stage="intake", state="passed",
        observed_at="2026-08-15T18:00:00Z", issue=1, repository="owner/repo"
    )
    assert ev.repository == "owner/repo"
    # Invalid repository (no slash)
    with raises(ValueError):
        WorkflowEvent(
            event_id="e2", operation_id="op1", stage="intake", state="passed",
            observed_at="2026-08-15T18:00:00Z", issue=1, repository="invalid"
        )
    # Invalid repository (spaces)
    with raises(ValueError):
        WorkflowEvent(
            event_id="e3", operation_id="op1", stage="intake", state="passed",
            observed_at="2026-08-15T18:00:00Z", issue=1, repository="owner /repo"
        )


def test_workflow_legacy_grouping_when_session_or_repository_missing():
    events = [
        event("one", "intake", "passed", "2026-08-15T18:00:00Z", operation_id="op-1"),
        event("two", "intake", "passed", "2026-08-15T18:01:00Z", operation_id="op-1"),
    ]
    operations = derive_operations(events)
    assert len(operations) == 1
    assert operations[0]["session_id"] is None
    assert operations[0]["repository"] is None


def test_jsonl_backward_compatibility_with_from_dict():
    # Old JSONL without session_id and repository should still deserialize
    old_data = {
        "event_id": "e1",
        "operation_id": "op1",
        "stage": "intake",
        "state": "passed",
        "observed_at": "2026-08-15T18:00:00Z",
        "issue": 1,
        "schema_version": 1,
    }
    ev = WorkflowEvent.from_dict(old_data)
    assert ev.session_id is None
    assert ev.repository is None
