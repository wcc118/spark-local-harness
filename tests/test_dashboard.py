import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from codex_local_harness.dashboard import DashboardSource, make_handler, render_dashboard
from codex_local_harness.diagnostics import Adjudication, LifecycleEvent, append_record
from codex_local_harness.health import RunHealth, TokenUsage, append_jsonl


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
