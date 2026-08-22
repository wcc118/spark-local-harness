import pytest

from codex_local_harness.workflow import WorkflowEvent, derive_operations, validate_append


def event(identifier, stage, state, observed_at, **changes):
    values = {"event_id": identifier, "operation_id": "issue-37", "stage": stage, "state": state, "observed_at": observed_at, "issue": 37}
    values.update(changes)
    return WorkflowEvent(**values)


def test_workflow_event_accepts_session_id_and_repository():
    ev = event("one", "intake", "passed", "2026-08-15T18:00:00Z", session_id="session-123", repository="owner/repo")
    assert ev.session_id == "session-123"
    assert ev.repository == "owner/repo"


def test_reviewer_with_pull_request_can_bootstrap_observed_operation():
    started = event(
        "review-started", "review", "active", "2026-08-15T18:00:00Z",
        role="reviewer", pull_request=42, run_id="review-run",
    )
    validate_append([], started)
    validate_append([started], event(
        "review-passed", "review", "passed", "2026-08-15T18:01:00Z",
        role="reviewer", pull_request=42, run_id="review-run",
    ))


@pytest.mark.parametrize("changes", [
    {},
    {"role": "coder", "pull_request": 42},
    {"role": "reviewer"},
])
def test_unbounded_review_cannot_bootstrap_observed_operation(changes):
    with pytest.raises(ValueError, match="new operation must begin"):
        validate_append([], event(
            "review-started", "review", "active", "2026-08-15T18:00:00Z", **changes,
        ))


@pytest.mark.parametrize("stage", ["delivery", "merge", "post-merge"])
def test_late_milestones_cannot_bootstrap_observed_operation(stage):
    with pytest.raises(ValueError, match="new operation must begin"):
        validate_append([], event(
            f"{stage}-started", stage, "active", "2026-08-15T18:00:00Z",
            role="reviewer", pull_request=42,
        ))


def test_workflow_derives_current_position_and_stage_history():
    operations = derive_operations([
        event("one", "intake", "passed", "2026-08-15T18:00:00Z"),
        event("two", "routing", "passed", "2026-08-15T18:01:00Z"),
        event("three", "local-work", "active", "2026-08-15T18:02:00Z", run_id="run-37", role="coder", model="qwen"),
    ])
    operation = operations[0]
    assert (operation["current_stage"], operation["current_state"], operation["run_id"]) == ("local-work", "active", "run-37")
    assert [stage["state"] for stage in operation["stages"][:4]] == ["passed", "passed", "active", "pending"]


def test_workflow_retains_every_run_identity_for_session_filtering():
    operation = derive_operations([
        event("one", "local-work", "failed", "2026-08-15T18:00:00Z", run_id="run-1", session_id="session-1"),
        event("two", "correction", "passed", "2026-08-15T18:01:00Z", run_id="run-2", session_id="session-1"),
    ])[0]
    assert operation["run_ids"] == ["run-1", "run-2"]


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


def test_truthful_milestones_do_not_infer_missing_predecessors():
    operation = derive_operations([
        event("evidence-first", "evidence", "active", "2026-08-15T18:00:00Z"),
    ])[0]
    states = {item["milestone"]: item["state"] for item in operation["milestones"]}
    assert set(states.values()) == {"pending"}
    assert operation["activities"] == [{
        "activity_type": "evidence", "milestone": "unassigned", "state": "active",
        "observed_at": "2026-08-15T18:00:00Z", "run_id": None, "role": None,
        "correction_cycle": 0, "stop_reason": None, "evidence_refs": [],
    }]


def test_correction_is_numbered_activity_loop_not_linear_milestone():
    operation = derive_operations([
        event("work", "local-work", "passed", "2026-08-15T18:00:00Z"),
        event("review", "review", "failed", "2026-08-15T18:01:00Z"),
        event("fix", "correction", "active", "2026-08-15T18:02:00Z", correction_cycle=1),
    ])[0]
    assert "correction" not in [item["milestone"] for item in operation["milestones"]]
    assert operation["activities"][-1]["milestone"] == "implementation"
    assert operation["activities"][-1]["correction_cycle"] == 1


def test_conflicting_event_reuse_and_stale_file_order_remain_visible():
    operation = derive_operations([
        event("same", "review", "passed", "2026-08-15T18:01:00Z"),
        event("same", "review", "failed", "2026-08-15T18:00:00Z"),
    ])[0]
    assert operation["current_state"] == "conflicting"
    assert {item["kind"] for item in operation["diagnostics"]} >= {
        "conflicting-event-reuse", "stale-event-order", "github-label-unavailable",
    }


def test_github_label_divergence_is_diagnostic_only():
    operation = derive_operations([
        event("merge", "merge", "active", "2026-08-15T18:00:00Z", evidence_refs=("github-label:status/backlog",)),
    ])[0]
    assert any(item["kind"] == "github-label-divergence" for item in operation["diagnostics"])
    assert operation["current_state"] == "active"


def test_historical_matching_label_cannot_hide_current_divergence():
    operation = derive_operations([
        event("old", "merge", "active", "2026-08-15T18:00:00Z", evidence_refs=("github-label:status/review-passed",)),
        event("new", "merge", "active", "2026-08-15T18:01:00Z", evidence_refs=("github-label:status/backlog",)),
    ])[0]
    diagnostic = next(item for item in operation["diagnostics"] if item["kind"] == "github-label-divergence")
    assert diagnostic["observed"] == ["status/backlog"]


def test_strict_publisher_rejects_reuse_regression_and_bad_correction_cycle():
    existing = [event("work", "local-work", "passed", "2026-08-15T18:00:00Z")]
    with pytest.raises(ValueError, match="event_id reuse"):
        validate_append(existing, event("work", "review", "active", "2026-08-15T18:01:00Z"))
    with pytest.raises(ValueError, match="regression"):
        validate_append(existing, event("back", "routing", "active", "2026-08-15T18:01:00Z"))
    with pytest.raises(ValueError, match="positive correction_cycle"):
        validate_append(existing, event("fix", "correction", "active", "2026-08-15T18:01:00Z"))
