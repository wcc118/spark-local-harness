import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from codex_local_harness.dashboard import DashboardSource, make_handler, render_dashboard
from codex_local_harness.diagnostics import Adjudication, LifecycleEvent, append_record
from codex_local_harness.health import RunHealth, TokenUsage, append_jsonl
from codex_local_harness.frontier import FrontierStatus
from codex_local_harness.workflow import WorkflowEvent


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
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(urllib.request.Request(base + "/", data=b"", method="POST"))
        assert error.value.code == 405
    finally:
        server.shutdown()
        server.server_close()


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
    assert "Unavailable" in page
    assert "Interactive Codex tokens" in page


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

    assert "fetch('/', {cache: 'no-store'})" in DASHBOARD_JS
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
    assert "runs=malformed" in page


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
    source = DashboardSource(base.runs, base.adjudications, base.lifecycle, workflow)
    snapshot = source.snapshot()
    
    # Machine-readable age should be present
    assert snapshot["freshness"]["newest_record_age_seconds"] is not None
    assert snapshot["freshness"]["newest_record_age_seconds"] >= 0
    
    # Human-readable age should be present in HTML
    page = render_dashboard(snapshot)
    assert "ago" in page
    assert "Source update age" in page


def test_snapshot_includes_machine_readable_age_value(tmp_path):
    from datetime import datetime, timezone
    
    base = fixture_source(tmp_path)
    workflow = tmp_path / "workflow.jsonl"
    # Use a timestamp that's 300 seconds ago
    recent_time = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    append_record(workflow, WorkflowEvent("evt-1", "issue-19", "review", "failed", recent_time, 19, stop_reason="test"))
    source = DashboardSource(base.runs, base.adjudications, base.lifecycle, workflow)
    snapshot = source.snapshot()
    
    # Verify machine-readable age is within reasonable bounds (should be < 10 seconds)
    age_seconds = snapshot["freshness"]["newest_record_age_seconds"]
    assert age_seconds is not None
    assert 0 <= age_seconds < 10


def test_no_sources_returns_no_data_state(tmp_path):
    source = DashboardSource(
        tmp_path / "missing-runs.jsonl",
        tmp_path / "missing-adj.jsonl",
        tmp_path / "missing-life.jsonl",
    )
    snapshot = source.snapshot()
    
    # Should have no data state
    assert snapshot["freshness"]["newest_record_age_seconds"] is None
    assert snapshot["freshness"]["newest_mtime_epoch"] is None
    assert snapshot["freshness"]["record_count"] == 0
    
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
    
    # Even though files exist, no records with timestamps means no data
    assert snapshot["freshness"]["newest_record_age_seconds"] is None
    assert snapshot["freshness"]["record_count"] == 0
    
    page = render_dashboard(snapshot)
    assert "No data" in page


def test_source_update_age_with_frontier_status(tmp_path):
    base = fixture_source(tmp_path)
    frontier = tmp_path / "frontier.jsonl"
    # Use a timestamp that's 5 seconds ago
    import time
    from datetime import datetime, timezone
    recent_time = datetime.fromtimestamp(time.time() - 5, timezone.utc).isoformat().replace("+00:00", "Z")
    append_record(frontier, FrontierStatus("status-1", recent_time, "unavailable", note_code="test"))
    source = DashboardSource(base.runs, base.adjudications, base.lifecycle, frontier=frontier)
    snapshot = source.snapshot()
    
    # Should use the frontier status timestamp
    age_seconds = snapshot["freshness"]["newest_record_age_seconds"]
    assert age_seconds is not None
    assert age_seconds >= 0
    assert age_seconds < 10  # Within 10 seconds
    
    page = render_dashboard(snapshot)
    # Either "ago" or "just now" is acceptable
    assert "ago" in page or "just now" in page
    # Verify data attribute in HTML
    assert 'data-age-seconds=' in page
