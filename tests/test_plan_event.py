import json
from datetime import datetime, timezone

import pytest

from codex_local_harness.plan_event import PlanEvent, derive_plans, read_plan_records, validate_append


def event(identifier="plan-1", revision=1, observed_at="2026-08-16T06:00:00Z", **changes):
    values = dict(
        event_id=identifier, operation_id="issue-55", issue=55,
        objective="Publish truthful plan state",
        steps=(
            {"step_id": "build", "summary": "Build the schema", "state": "completed", "evidence_refs": ["test:schema"]},
            {"step_id": "smoke", "summary": "Run a live smoke", "state": "active"},
            {"step_id": "merge", "summary": "Review and merge", "state": "pending"},
        ),
        observed_at=observed_at, source="codex", revision=revision,
        session_id="thread-a", repository="owner/repo",
    )
    values.update(changes)
    return PlanEvent(**values)


def test_plan_schema_accepts_arbitrary_ordered_steps_but_only_one_active():
    plan = event()
    assert [step["step_id"] for step in plan.steps] == ["build", "smoke", "merge"]
    with pytest.raises(ValueError, match="at most one active"):
        event(steps=(
            {"step_id": "one", "summary": "One", "state": "active"},
            {"step_id": "two", "summary": "Two", "state": "active"},
        ))


def test_revision_requires_exact_increment_and_parent():
    first = event()
    second = event("plan-2", 2, "2026-08-16T06:01:00Z", parent_event_id="plan-1", revision_reason="A failed smoke changed the next step")
    validate_append([first], second)
    with pytest.raises(ValueError, match="advance exactly once"):
        validate_append([first], event("plan-3", 3, "2026-08-16T06:02:00Z", parent_event_id="plan-1"))
    with pytest.raises(ValueError, match="canonical parent"):
        validate_append([first], event("plan-x", 2, "2026-08-16T06:02:00Z", parent_event_id="wrong"))


def test_derivation_retains_history_conflicts_stale_and_superseded_revision():
    first = event()
    second = event("plan-2", 2, "2026-08-16T06:01:00Z", parent_event_id="plan-1", revision_reason="New evidence")
    conflicting = event("plan-2", 2, "2026-08-16T06:00:30Z", parent_event_id="plan-1", objective="Conflicting objective")
    plan = derive_plans([second, first, conflicting])[0]
    assert plan["revision"] == 2
    assert plan["completed_steps"] == 1
    assert len(plan["history"]) == 3
    assert {item["kind"] for item in plan["diagnostics"]} >= {
        "conflicting-event-reuse", "conflicting-revision", "stale-event-order",
    }


def test_active_old_plan_is_explicitly_stale():
    plan = derive_plans([event()], now=datetime(2026, 8, 16, 7, 0, tzinfo=timezone.utc))[0]
    assert plan["stale"] is True
    assert any(item["kind"] == "stale-plan" for item in plan["diagnostics"])


def test_reader_rejects_malformed_plan_without_modifying_file(tmp_path):
    path = tmp_path / "plans.jsonl"
    path.write_text(json.dumps(event().as_dict()) + "\n", encoding="utf-8")
    before = path.read_bytes()
    assert read_plan_records(path)[0].event_id == "plan-1"
    assert path.read_bytes() == before
    path.write_text("not-json\n", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        read_plan_records(path)
