import json
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer

import pytest

from codex_local_harness.dashboard import DashboardSource, make_handler, render_dashboard
from codex_local_harness.diagnostics import Adjudication, LifecycleEvent, PublicationFailure, append_record
from codex_local_harness.health import RunHealth, TokenUsage, append_jsonl
from codex_local_harness.frontier import FrontierStatus
from codex_local_harness.workflow import WorkflowEvent
from codex_local_harness.operation_usage import OperationUsageSnapshot
from codex_local_harness.plan_event import PlanEvent


def fixture_source(tmp_path):
    runs = tmp_path / "runs.jsonl"
    adjudications = tmp_path / "adjudications.jsonl"
    lifecycle = tmp_path / "lifecycle.jsonl"
    append_jsonl(runs, RunHealth(
        run_id="run-hidden", issue=19, role="coder", runtime="opencode", model="qwen",
        profile="v4", task_class="difficult", status="success", wall_seconds=12,
        usage=TokenUsage(local_input=100), delivery_ready=True,
    ))
    append_record(adjudications, Adjudication(
        adjudication_id="adj-hidden", run_id="run-hidden", verdict="failed", source="evaluator",
        observed_at="2026-08-15T17:00:00Z", failure_class="test-hidden",
        evidence_refs=("evaluator:hidden",),
    ))
    append_record(lifecycle, LifecycleEvent(
        event_id="run-hidden:completed", run_id="run-hidden", event="completed",
        observed_at="2026-08-15T16:59:00Z", wall_seconds=12, exit_status=0, dirty_entries=0,
    ))
    return DashboardSource(runs, adjudications, lifecycle)


def test_snapshot_and_html_show_claim_observation_contradiction(tmp_path):
    snapshot = fixture_source(tmp_path).snapshot()
    assert snapshot["read_only"] is True
    assert snapshot["summary"]["contradictions"] == 1
    page = render_dashboard(snapshot)
    assert "run-hidden" in page
    assert "success" in page
    assert "failed" in page
    assert 'class="contradiction"' in page


def test_dashboard_escapes_all_derived_text(tmp_path):
    snapshot = fixture_source(tmp_path).snapshot()
    snapshot["runs"][0]["model"] = "<script>alert(1)</script>"
    snapshot["runs"][0]["role"] = "<img src=x>"
    page = render_dashboard(snapshot)
    assert "<script>" not in page
    assert "&lt;script&gt;" in page
    assert "<img" not in page


def test_http_surface_is_get_only_and_no_store(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(fixture_source(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(base + "/api/snapshot") as response:
            payload = json.load(response)
            assert payload["read_only"] is True
            assert response.headers["Cache-Control"] == "no-store"
        with urllib.request.urlopen(base + "/favicon.svg") as response:
            assert response.headers["Content-Type"] == "image/svg+xml"
            assert b'<svg xmlns="http://www.w3.org/2000/svg"' in response.read()
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(base + "/", data=b"", method="POST"))
        assert error.value.code == 405
    finally:
        server.shutdown()
        server.server_close()


def test_dashboard_declares_distinctive_local_favicon(tmp_path):
    page = render_dashboard(fixture_source(tmp_path).snapshot())
    assert '<link rel="icon" href="/favicon.svg" type="image/svg+xml">' in page


def test_source_reads_without_modifying_ledgers(tmp_path):
    source = fixture_source(tmp_path)
    before = {path: path.read_bytes() for path in (source.runs, source.adjudications, source.lifecycle)}
    source.snapshot()
    assert {path: path.read_bytes() for path in before} == before


def test_snapshot_includes_workflow_and_explicitly_unavailable_codex_data(tmp_path):
    base = fixture_source(tmp_path)
    workflow = tmp_path / "workflow.jsonl"
    frontier = tmp_path / "frontier.jsonl"
    append_record(workflow, WorkflowEvent("evt-1", "issue-19", "review", "failed", "2026-08-15T18:00:00Z", 19, stop_reason="hidden-test"))
    append_record(frontier, FrontierStatus("status-1", "2026-08-15T18:00:00Z", "unavailable", note_code="official-interface-unavailable"))
    source = DashboardSource(base.runs, base.adjudications, base.lifecycle, workflow, frontier)
    snapshot = source.snapshot()
    assert snapshot["operations"][0]["current_stage"] == "review"
    assert snapshot["codex"]["source"] == "unavailable"
    assert snapshot["codex"]["context_used"] is None
    assert snapshot["codex"]["tokens_used"] is None
    page = render_dashboard(snapshot)
    assert "Current workflow" in page
    assert "Interactive Codex tokens" not in page
    assert "Codex context" not in page
    assert "Plan remaining" not in page


def test_sse_emits_snapshot_event(tmp_path):
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(fixture_source(tmp_path)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    response = urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/events", timeout=2)
    try:
        assert response.readline() == b"event: snapshot\n"
        assert response.readline() == b'data: {"changed":true}\n'
    finally:
        response.close()
        server.shutdown()
        server.server_close()


def test_browser_updates_dom_without_full_page_reload():
    from codex_local_harness.dashboard import DASHBOARD_JS

    assert "fetch('/' + window.location.search, {cache: 'no-store'})" in DASHBOARD_JS
    assert "replaceWith(replacement)" in DASHBOARD_JS
    assert "location.reload" not in DASHBOARD_JS


def test_missing_and_malformed_sources_are_visible(tmp_path):
    runs = tmp_path / "runs.jsonl"
    runs.write_text("not-json\n", encoding="utf-8")
    source = DashboardSource(runs, tmp_path / "missing-adj.jsonl", tmp_path / "missing-life.jsonl")
    snapshot = source.snapshot()
    assert {item["state"] for item in snapshot["sources"]} == {"malformed", "missing"}
    page = render_dashboard(snapshot)
    assert "Source warning" in page
    assert "runs · malformed" in page
    assert "check the last line for truncated or invalid JSON" in page


def test_superseded_operation_is_retained_but_not_current(tmp_path):
    base = fixture_source(tmp_path)
    workflow = tmp_path / "workflow.jsonl"
    append_record(workflow, WorkflowEvent("old-1", "old-chain", "local-work", "active", "2026-08-15T18:00:00Z", 19))
    append_record(workflow, WorkflowEvent("old-2", "old-chain", "post-merge", "superseded", "2026-08-15T18:01:00Z", 19, stop_reason="canonical-operation-replaced"))
    append_record(workflow, WorkflowEvent("new-1", "issue-19", "post-merge", "passed", "2026-08-15T18:02:00Z", 19))
    snapshot = DashboardSource(base.runs, base.adjudications, base.lifecycle, workflow).snapshot()
    assert [operation["operation_id"] for operation in snapshot["operations"]] == ["issue-19"]
    assert snapshot["superseded_operations"] == 1


def test_snapshot_includes_human_readable_source_update_age(tmp_path):
    base = fixture_source(tmp_path)
    workflow = tmp_path / "workflow.jsonl"
    append_record(workflow, WorkflowEvent("evt-1", "issue-19", "review", "failed", "2026-08-15T18:00:00Z", 19, stop_reason="hidden-test"))
    now = datetime(2026, 8, 15, 22, 0, tzinfo=timezone.utc)
    for path in (base.runs, base.adjudications, base.lifecycle, workflow):
        os.utime(path, (now.timestamp() - 300, now.timestamp() - 300))

    source = DashboardSource(base.runs, base.adjudications, base.lifecycle, workflow, now=now)
    snapshot = source.snapshot()

    assert snapshot["freshness"]["newest_age_seconds"] == 300
    assert snapshot["freshness"]["newest_mtime_epoch"] == now.timestamp() - 300
    page = render_dashboard(snapshot)
    assert "5 minutes ago" in page
    assert "Source update age" in page


def test_no_sources_returns_no_data_state(tmp_path):
    source = DashboardSource(
        tmp_path / "missing-runs.jsonl",
        tmp_path / "missing-adj.jsonl",
        tmp_path / "missing-life.jsonl",
    )
    snapshot = source.snapshot()

    assert snapshot["freshness"]["newest_age_seconds"] is None
    assert snapshot["freshness"]["newest_mtime_epoch"] is None
    page = render_dashboard(snapshot)
    assert "No data" in page


def test_empty_sources_returns_no_data_state(tmp_path):
    runs = tmp_path / "runs.jsonl"
    runs.write_text("", encoding="utf-8")
    adjudications = tmp_path / "adjudications.jsonl"
    adjudications.write_text("", encoding="utf-8")
    lifecycle = tmp_path / "lifecycle.jsonl"
    lifecycle.write_text("", encoding="utf-8")

    source = DashboardSource(runs, adjudications, lifecycle)
    snapshot = source.snapshot()

    assert snapshot["freshness"]["newest_age_seconds"] is None
    assert snapshot["freshness"]["existing_files"] == 3
    page = render_dashboard(snapshot)
    assert "No data" in page


def test_active_workflow_stage_has_orbiting_light(tmp_path):
    base = fixture_source(tmp_path)
    workflow = tmp_path / "workflow.jsonl"
    append_record(workflow, WorkflowEvent("evt-1", "issue-19", "review", "active", "2026-08-15T18:00:00Z", 19))
    source = DashboardSource(base.runs, base.adjudications, base.lifecycle, workflow)
    snapshot = source.snapshot()
    page = render_dashboard(snapshot)
    assert 'class="milestone active"' in page
    assert '.milestone.active::after{content:""' in page
    assert ".milestone.active{border-color:var(--warn)}" in page
    assert "top:4px;left:4px;width:6px;height:6px" in page
    assert "top:calc(100% - 10px);left:calc(100% - 10px)" in page
    assert "animation:milestone-orbit 4s linear infinite" in page


def test_orbiting_light_respects_prefers_reduced_motion(tmp_path):
    base = fixture_source(tmp_path)
    workflow = tmp_path / "workflow.jsonl"
    append_record(workflow, WorkflowEvent("evt-1", "issue-19", "review", "active", "2026-08-15T18:00:00Z", 19))
    source = DashboardSource(base.runs, base.adjudications, base.lifecycle, workflow)
    snapshot = source.snapshot()
    page = render_dashboard(snapshot)
    assert "@media (prefers-reduced-motion:no-preference){.milestone.active::after{animation:milestone-orbit" in page
    assert "@media (prefers-reduced-motion:reduce){.milestone.active::after{top:4px;right:4px;left:auto}" in page


def test_non_active_stages_do_not_show_orbit_light(tmp_path):
    base = fixture_source(tmp_path)
    workflow = tmp_path / "workflow.jsonl"
    # Add a passed stage and an active stage
    append_record(workflow, WorkflowEvent("evt-1", "issue-19", "intake", "passed", "2026-08-15T17:55:00Z", 19))
    append_record(workflow, WorkflowEvent("evt-2", "issue-19", "review", "active", "2026-08-15T18:00:00Z", 19))
    source = DashboardSource(base.runs, base.adjudications, base.lifecycle, workflow)
    snapshot = source.snapshot()
    page = render_dashboard(snapshot)
    # Should have both passed and active stages
    assert 'class="milestone passed"' in page
    assert 'class="milestone active"' in page
    assert 'class="milestone pending"' in page
    assert ".milestone.pending{color:#626b7c;border-color:#343946;background:#181b22}" in page


def test_not_applicable_stage_is_rendered_as_not_needed(tmp_path):
    base = fixture_source(tmp_path)
    workflow = tmp_path / "workflow.jsonl"
    append_record(workflow, WorkflowEvent("evt-1", "issue-19", "correction", "not-applicable", "2026-08-15T18:00:00Z", 19))
    page = render_dashboard(DashboardSource(base.runs, base.adjudications, base.lifecycle, workflow).snapshot())
    assert 'class="milestone-activity not-applicable"' in page
    assert "correction · not needed" in page
    assert ".milestone.not-applicable{color:#737b8c;border-color:#343946;background:transparent}" in page


def test_render_dashboard_emits_single_title(tmp_path):
    base = fixture_source(tmp_path)
    snapshot = base.snapshot()
    page = render_dashboard(snapshot)
    # Regression assertion: exactly one title element with the expected text
    assert page.count("<title>") == 1
    assert page.count("</title>") == 1
    assert "<title>Codex agent harness diagnostics</title>" in page


def test_publication_failures_are_visible_without_hiding_valid_runs(tmp_path):
    base = fixture_source(tmp_path)
    failures = tmp_path / "publication-failures.jsonl"
    append_record(failures, PublicationFailure(
        "run-1:finish:health",
        "run-1",
        "finish",
        "health",
        "ingestion-failed",
        "2026-08-16T00:00:00Z",
    ))
    snapshot = DashboardSource(
        base.runs,
        base.adjudications,
        base.lifecycle,
        publication_failures=failures,
    ).snapshot()
    assert snapshot["summary"]["runs"] == 1
    assert len(snapshot["publication_failures"]) == 1
    assert "Telemetry publication failures:</strong> 1" in render_dashboard(snapshot)


def test_session_filter_separates_same_issue_and_survives_reconstruction(tmp_path):
    base = fixture_source(tmp_path)
    workflow = tmp_path / "workflow.jsonl"
    append_record(workflow, WorkflowEvent(
        "a", "issue-19", "local-work", "active", "2026-08-16T00:00:00Z", 19,
        run_id="run-1", session_id="thread-a", repository="owner/repo",
    ))
    append_record(workflow, WorkflowEvent(
        "b", "issue-19", "post-merge", "passed", "2026-08-16T00:01:00Z", 19,
        session_id="thread-b", repository="owner/repo",
    ))
    source = DashboardSource(base.runs, base.adjudications, base.lifecycle, workflow)

    all_sessions = source.snapshot()
    selected = DashboardSource(base.runs, base.adjudications, base.lifecycle, workflow).snapshot("thread-a")

    assert len(all_sessions["operations"]) == 2
    assert {(item["session_id"], item["status"]) for item in all_sessions["sessions"]} == {
        ("thread-a", "active"), ("thread-b", "completed"),
    }
    assert [item["session_id"] for item in selected["operations"]] == ["thread-a"]
    assert selected["selected_session"] == "thread-a"


def test_valid_workflow_identity_links_exact_github_issue(tmp_path):
    base = fixture_source(tmp_path)
    workflow = tmp_path / "workflow.jsonl"
    append_record(workflow, WorkflowEvent(
        "linked", "issue-62", "review", "active", "2026-08-16T00:00:00Z", 62,
        session_id="thread-a", repository="wcc118/codex-local-harness",
    ))
    page = render_dashboard(DashboardSource(base.runs, base.adjudications, base.lifecycle, workflow).snapshot())
    assert 'href="https://github.com/wcc118/codex-local-harness/issues/62"' in page
    assert 'target="_blank" rel="noopener noreferrer"' in page


def test_operation_usage_drives_division_of_labor_without_cost_claim(tmp_path):
    base = fixture_source(tmp_path)
    usage = tmp_path / "operation-usage.jsonl"
    common = dict(
        session_id="thread-a", repository="owner/repo", issue=19,
        operation_id="issue-19", source="codex-rollout-token-count",
        context_window=258400,
    )
    append_record(usage, OperationUsageSnapshot(
        "start", "2026-08-16T00:00:00Z", "start",
        input_tokens=100, cached_input_tokens=60, cache_write_input_tokens=0,
        output_tokens=20, reasoning_output_tokens=5, total_tokens=120, **common,
    ))
    append_record(usage, OperationUsageSnapshot(
        "finish", "2026-08-16T00:05:00Z", "finish",
        input_tokens=300, cached_input_tokens=180, cache_write_input_tokens=0,
        output_tokens=50, reasoning_output_tokens=15, total_tokens=350, **common,
    ))
    snapshot = DashboardSource(
        base.runs, base.adjudications, base.lifecycle, operation_usage=usage,
    ).snapshot()
    page = render_dashboard(snapshot)
    assert snapshot["accounting"]["codex_input"] == 200
    assert snapshot["accounting"]["codex_cached"] == 120
    assert "Processed context · local share" in page
    assert "Generated + reasoning · local share" in page
    assert "Cost share" not in page


def test_missing_cache_is_not_rendered_as_observed_zero(tmp_path):
    base = fixture_source(tmp_path)
    page = render_dashboard(base.snapshot())
    assert "Not reported (unsupported)" in page


def test_correction_loop_and_activity_evidence_render_accessibly(tmp_path):
    base = fixture_source(tmp_path)
    workflow = tmp_path / "workflow.jsonl"
    append_record(workflow, WorkflowEvent("work", "issue-35", "local-work", "passed", "2026-08-15T18:00:00Z", 35))
    append_record(workflow, WorkflowEvent("review", "issue-35", "review", "failed", "2026-08-15T18:01:00Z", 35))
    append_record(workflow, WorkflowEvent("fix", "issue-35", "correction", "active", "2026-08-15T18:02:00Z", 35, correction_cycle=1))
    page = render_dashboard(DashboardSource(base.runs, base.adjudications, base.lifecycle, workflow).snapshot())
    assert 'aria-label="Delivery milestones"' in page
    assert 'aria-label="Correction loop cycle 1"' in page
    assert "↩ correction cycle 1 → implementation · active" in page
    assert "grid-template-columns:repeat(4,minmax(0,1fr))" in page
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in page


def test_evidence_first_case_has_pending_milestones_and_visible_activity(tmp_path):
    base = fixture_source(tmp_path)
    workflow = tmp_path / "workflow.jsonl"
    append_record(workflow, WorkflowEvent("evidence", "issue-35", "evidence", "active", "2026-08-15T18:00:00Z", 35))
    snapshot = DashboardSource(base.runs, base.adjudications, base.lifecycle, workflow).snapshot()
    assert all(item["state"] == "pending" for item in snapshot["operations"][0]["milestones"])
    page = render_dashboard(snapshot)
    assert "evidence · active" in page
    assert 'aria-label="Unassigned activities"' in page
    assert page.count('class="milestone pending"') == 8


def test_running_plan_renders_below_matching_workflow_and_escapes_public_text(tmp_path):
    base = fixture_source(tmp_path)
    workflow = tmp_path / "workflow.jsonl"
    plans = tmp_path / "plans.jsonl"
    append_record(workflow, WorkflowEvent(
        "workflow", "issue-55", "local-work", "active", "2026-08-16T06:00:00Z", 55,
        session_id="thread-a", repository="owner/repo",
    ))
    append_record(plans, PlanEvent(
        "plan-1", "issue-55", 55, "Build <truthfully>", (
            {"step_id": "one", "summary": "Schema", "state": "completed", "evidence_refs": ["test:schema"]},
            {"step_id": "two", "summary": "Live smoke", "state": "active"},
            {"step_id": "three", "summary": "Merge", "state": "pending"},
        ), "2026-08-16T06:00:00Z", "codex", session_id="thread-a", repository="owner/repo",
    ))
    snapshot = DashboardSource(base.runs, base.adjudications, base.lifecycle, workflow, plans=plans).snapshot()
    page = render_dashboard(snapshot)
    assert page.count('class="operation-pair"') == 1
    assert "Running plan" in page and "1 / 3" in page
    assert "current: Live smoke" in page
    assert "Build &lt;truthfully&gt;" in page and "Build <truthfully>" not in page
    assert 'class="plan-step active"' in page
    assert 'class="plan-step pending"' in page
    assert "Revision history (1)" in page
    assert "grid-template-columns:minmax(0,1fr)" in page
    assert "grid-template-columns:repeat(8,minmax(0,1fr))" in page
    assert "repeat(8,minmax(120px,1fr))" not in page
    assert "@media (prefers-reduced-motion:no-preference)" in page


def test_missing_plan_is_omitted_and_malformed_source_is_warning(tmp_path):
    base = fixture_source(tmp_path)
    workflow = tmp_path / "workflow.jsonl"
    append_record(workflow, WorkflowEvent("workflow", "issue-55", "review", "active", "2026-08-16T06:00:00Z", 55))
    missing_page = render_dashboard(DashboardSource(base.runs, base.adjudications, base.lifecycle, workflow, plans=tmp_path / "missing.jsonl").snapshot())
    assert "Running plan" not in missing_page
    assert "Plan unavailable" not in missing_page
    malformed = tmp_path / "plans.jsonl"
    malformed.write_text("not-json\n", encoding="utf-8")
    malformed_page = render_dashboard(DashboardSource(base.runs, base.adjudications, base.lifecycle, workflow, plans=malformed).snapshot())
    assert "plans · malformed" in malformed_page


def test_plan_source_participates_in_sse_fingerprint(tmp_path):
    base = fixture_source(tmp_path)
    plans = tmp_path / "plans.jsonl"
    plans.write_text("", encoding="utf-8")
    source = DashboardSource(base.runs, base.adjudications, base.lifecycle, plans=plans)
    before = source.fingerprint()
    plans.write_text("\n", encoding="utf-8")
    assert source.fingerprint() != before


def test_plan_only_session_is_selectable_without_fabricating_workflow(tmp_path):
    base = fixture_source(tmp_path)
    plans = tmp_path / "plans.jsonl"
    append_record(plans, PlanEvent(
        "plan-only", "issue-55", 55, "Plan before workflow", (
            {"step_id": "one", "summary": "Await workflow", "state": "active"},
        ), "2026-08-16T06:00:00Z", "codex", session_id="plan-session", repository="owner/repo",
    ))
    source = DashboardSource(base.runs, base.adjudications, base.lifecycle, plans=plans)
    snapshot = source.snapshot("plan-session")
    assert snapshot["selected_session"] == "plan-session"
    assert snapshot["operations"] == []
    assert [item["session_id"] for item in snapshot["plans"]] == ["plan-session"]
    assert any(item["session_id"] == "plan-session" and item["operations"] == 0 for item in snapshot["sessions"])
    assert "Workflow unavailable for this plan." in render_dashboard(snapshot)


def test_run_rows_carry_triage_bucket_and_search_attributes(tmp_path):
    base = fixture_source(tmp_path)  # run-hidden: claimed success, observed failed -> contradiction
    append_jsonl(base.runs, RunHealth(
        run_id="run-open", issue=21, role="reviewer", runtime="opencode", model="qwen",
        profile="v4", task_class="easy", status="success", wall_seconds=4,
        usage=TokenUsage(local_input=50), delivery_ready=True,
    ))
    snapshot = base.snapshot()
    page = render_dashboard(snapshot)
    assert 'data-triage="contradiction"' in page
    assert 'data-triage="needs-judgment"' in page
    assert 'data-search="run-open 21 reviewer qwen easy success unadjudicated' in page
    assert "Needs judgment · 1" in page
    assert "Contradictions · 1" in page
    assert "All · 2" in page
    assert 'data-details-id="run:run-hidden"' in page


def test_freshness_chip_escalates_from_source_age(tmp_path):
    base = fixture_source(tmp_path)
    now = datetime(2026, 8, 15, 22, 0, tzinfo=timezone.utc)
    fresh_time = now.timestamp() - 30
    for path in (base.runs, base.adjudications, base.lifecycle):
        os.utime(path, (fresh_time, fresh_time))
    fresh_page = render_dashboard(DashboardSource(base.runs, base.adjudications, base.lifecycle, now=now).snapshot())
    assert 'class="freshness good"' in fresh_page

    stale_time = now.timestamp() - 3600
    for path in (base.runs, base.adjudications, base.lifecycle):
        os.utime(path, (stale_time, stale_time))
    stale_page = render_dashboard(DashboardSource(base.runs, base.adjudications, base.lifecycle, now=now).snapshot())
    assert 'class="freshness critical"' in stale_page

    empty_page = render_dashboard(DashboardSource(
        tmp_path / "missing-runs.jsonl", tmp_path / "missing-adj.jsonl", tmp_path / "missing-life.jsonl",
    ).snapshot())
    assert 'class="freshness unknown"' in empty_page


def test_current_workflow_collapses_milestones_into_a_dot_rail(tmp_path):
    base = fixture_source(tmp_path)
    workflow = tmp_path / "workflow.jsonl"
    append_record(workflow, WorkflowEvent("evt-1", "issue-19", "review", "active", "2026-08-15T18:00:00Z", 19))
    page = render_dashboard(DashboardSource(base.runs, base.adjudications, base.lifecycle, workflow).snapshot())
    assert '<details class="flow-toggle" data-details-id="flow:' in page
    assert 'class="flow-summary"' in page
    assert 'class="rail-dot active"' in page
    assert 'class="flow" aria-label="Delivery milestones"' in page  # unchanged, still present inside the toggle


def test_publication_failures_expand_in_place_with_details(tmp_path):
    base = fixture_source(tmp_path)
    failures = tmp_path / "publication-failures.jsonl"
    append_record(failures, PublicationFailure(
        "run-1:finish:health", "run-1", "finish", "health", "ingestion-failed", "2026-08-16T00:00:00Z",
    ))
    snapshot = DashboardSource(base.runs, base.adjudications, base.lifecycle, publication_failures=failures).snapshot()
    page = render_dashboard(snapshot)
    assert '<details class="source-warning publication-failures">' in page
    assert "Telemetry publication failures:</strong> 1" in page
    assert "finish → health" in page
    assert "ingestion-failed" in page


def test_accounting_footnote_replaces_cached_card_and_sparkline_renders(tmp_path):
    base = fixture_source(tmp_path)
    append_jsonl(base.runs, RunHealth(
        run_id="run-open", issue=21, role="reviewer", runtime="opencode", model="qwen",
        profile="v4", task_class="easy", status="success", wall_seconds=4,
        usage=TokenUsage(local_input=5000), delivery_ready=True,
    ))
    page = render_dashboard(base.snapshot())
    assert '<p class="accounting-footnote">' in page
    assert "Not reported (unsupported)" in page
    assert "<span>Cached input</span>" not in page
    assert '<svg class="sparkline"' in page


def test_session_picker_has_no_csp_blocked_inline_handler(tmp_path):
    base = fixture_source(tmp_path)
    page = render_dashboard(base.snapshot())
    assert "onchange" not in page
    assert '<select name="session" id="session-select">' in page
    assert '<button type="submit" class="session-submit">Go</button>' in page
    from codex_local_harness.dashboard import DASHBOARD_JS
    assert "event.target.id === 'session-select'" in DASHBOARD_JS
    assert "event.target.form.submit()" in DASHBOARD_JS


def test_session_options_show_a_readable_label_not_a_bare_id(tmp_path):
    base = fixture_source(tmp_path)
    workflow = tmp_path / "workflow.jsonl"
    append_record(workflow, WorkflowEvent(
        "a", "issue-19", "local-work", "active", "2026-08-16T00:00:00Z", 19,
        run_id="run-1", session_id="thread-a", repository="owner/repo",
    ))
    append_record(workflow, WorkflowEvent(
        "b", "issue-19", "post-merge", "passed", "2026-08-16T00:01:00Z", 19,
    ))  # no session_id -> falls into the "legacy" bucket
    page = render_dashboard(DashboardSource(base.runs, base.adjudications, base.lifecycle, workflow).snapshot())
    assert "owner/repo#19 · active · 1 ops · thread-a" in page
    assert "Legacy (no session ID) · completed · 1 ops</option>" in page
