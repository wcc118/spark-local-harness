from codex_local_harness.diagnostics import Adjudication, LifecycleEvent
from codex_local_harness.health import RunHealth, TokenUsage
from codex_local_harness.read_model import build_run_views, summarize_views


def run(run_id, **changes):
    values = {
        "run_id": run_id, "issue": 19, "role": "coder", "runtime": "opencode",
        "model": "qwen3-coder-next", "profile": "codex-led-v4",
        "task_class": "difficult-concurrency", "status": "success",
        "wall_seconds": 150.393, "usage": TokenUsage(local_input=470_000, local_output=7_329),
        "delivery_ready": True,
    }
    values.update(changes)
    return RunHealth(**values)


def test_hidden_failure_remains_a_visible_contradiction():
    judgment = Adjudication(
        adjudication_id="adj-hidden", run_id="first-pass", verdict="failed", source="evaluator",
        observed_at="2026-08-15T17:00:00Z", failure_class="implementation-reader-atomicity",
        intervention_depth="audit", codex_decision="CORRECTION",
        evidence_refs=("evaluator:case4_hidden",),
    )
    view = build_run_views([run("first-pass")], [judgment])[0]
    assert (view.claimed_status, view.observed_outcome, view.effective_outcome) == ("success", "failed", "failed")
    assert view.contradiction is True


def test_interrupted_correction_exists_without_worker_health_record():
    event = LifecycleEvent(
        event_id="correction:interrupted", run_id="correction", event="interrupted",
        observed_at="2026-08-15T17:12:00Z", wall_seconds=681, exit_status=130, dirty_entries=1,
        issue=19, role="coder", model="qwen3-coder-next", profile="v4", task_class="difficult",
    )
    view = build_run_views([], [], [event])[0]
    assert view.claimed_status is None
    assert (view.lifecycle_state, view.effective_outcome, view.wall_seconds) == ("interrupted", "interrupted", 681)
    assert (view.lifecycle_exit_status, view.lifecycle_dirty_entries) == (130, 1)
    assert (view.issue, view.role, view.model, view.task_class) == (19, "coder", "qwen3-coder-next", "difficult")


def test_successful_adjudication_can_contradict_worker_failure():
    judgment = Adjudication(
        adjudication_id="adj-pass", run_id="recovered", verdict="passed", source="evaluator",
        observed_at="2026-08-15T17:00:00Z",
    )
    view = build_run_views([run("recovered", status="failure", delivery_ready=False)], [judgment])[0]
    assert view.contradiction is True


def test_unadjudicated_success_does_not_inflate_confirmed_autonomy():
    routine = run("routine", task_class="routine", usage=TokenUsage(local_input=100))
    moderate = run("moderate", task_class="moderate", usage=TokenUsage(local_input=200))
    judgment = Adjudication(
        adjudication_id="adj-pass", run_id="moderate", verdict="passed", source="evaluator",
        observed_at="2026-08-15T17:00:00Z",
    )
    summary = summarize_views(build_run_views([routine, moderate], [judgment]))
    assert (summary["runs"], summary["adjudicated_runs"], summary["unadjudicated_runs"]) == (2, 1, 1)
    assert summary["confirmed_local_autonomy_rate"] == 1.0


def test_correction_lineage_and_codex_intervention_are_derived():
    judgment = Adjudication(
        adjudication_id="adj-stop", run_id="correction", verdict="interrupted", source="codex",
        observed_at="2026-08-15T17:12:00Z", correction_of="first-pass", correction_cycle=1,
        intervention_depth="targeted", codex_decision="INTERVENTION",
    )
    views = build_run_views([run("correction", status="failure", delivery_ready=False)], [judgment])
    assert views[0].correction_of == "first-pass"
    assert summarize_views(views)["correction_cycles"] == 1
    assert summarize_views(views)["codex_interventions"] == 1
