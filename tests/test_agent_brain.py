from __future__ import annotations

from codex_local_harness.dashboard import DASHBOARD_JS, render_dashboard
from test_dashboard import fixture_source


def _projection(*, state: str = "waiting-inference", source_state: str = "ok") -> dict:
    return {
        "schema_version": 1,
        "observed_at": "2026-08-22T12:00:10+00:00",
        "source_state": source_state,
        "nodes": [{
            "id": "worker:run-1",
            "kind": "worker",
            "label": "coder run-1",
            "state": state,
            "observed_at": "2026-08-22T12:00:00+00:00",
            "source_ref": "v5-log:run-1.jsonl",
            "reason": "model step started",
            "role": "coder",
        }, {
            "id": "model:qwen",
            "kind": "model",
            "label": "Qwen",
            "state": "active",
            "observed_at": "2026-08-22T12:00:00+00:00",
            "source_ref": "v5-result:run-1.result.json",
            "reason": "model observed",
        }, {
            "id": "test:pytest",
            "kind": "test",
            "label": "test",
            "state": "running-tests",
            "observed_at": None,
            "source_ref": "v5-log:run-1.jsonl:2",
            "reason": "normalized tool event observed",
        }],
        "edges": [{
            "source": "worker:run-1",
            "target": "model:qwen",
            "kind": "inference",
            "state": state,
            "observed_at": "2026-08-22T12:00:00+00:00",
            "source_ref": "v5-log:run-1.jsonl",
            "reason": "awaiting model action",
        }],
        "diagnostics": [],
        "truncated": {"runs": 0, "nodes": 0, "edges": 0, "events": 0},
    }


def _page(tmp_path, projection: dict) -> str:
    snapshot = fixture_source(tmp_path).snapshot()
    snapshot["live_execution"] = projection
    return render_dashboard(snapshot)


def test_agent_brain_renders_semantic_regions_active_flow_and_details(tmp_path) -> None:
    page = _page(tmp_path, _projection())

    for heading in (
        "Intake + repository", "Codex executive", "Routing + inference",
        "Coder + action", "Review + verification", "Tools", "Durable memory",
    ):
        assert heading in page
    assert 'class="brain-node state-waiting-inference"' in page
    assert 'class="brain-edge state-waiting-inference"' in page
    assert "10 seconds ago" in page
    assert 'aria-live="polite"' in page
    assert 'type="button" id="brain-node-0"' in page


def test_agent_brain_state_changes_stale_unknown_and_partial_are_explicit(tmp_path) -> None:
    stale = _projection(state="stale", source_state="partial")
    stale["nodes"][1]["state"] = "invented-state"
    page = _page(tmp_path, stale)

    assert "state-stale" in page
    assert "state-unknown" in page
    assert "Evidence partial." in page
    assert "only what could be safely normalized" in page


def test_agent_brain_escapes_all_evidence_text(tmp_path) -> None:
    projection = _projection()
    projection["nodes"][0]["label"] = '<img src=x onerror="alert(1)">'
    projection["nodes"][0]["reason"] = "<script>steal()</script>"
    projection["edges"][0]["source"] = 'bad" autofocus onfocus="steal()'

    page = _page(tmp_path, projection)

    assert "<script>" not in page
    assert "<img src=x" not in page
    assert "&lt;script&gt;steal()&lt;/script&gt;" in page
    assert '&lt;img src=x onerror=&quot;' in page
    assert 'autofocus onfocus="steal()' not in page


def test_agent_brain_empty_state_teaches_without_inventing_idle(tmp_path) -> None:
    projection = _projection(source_state="missing")
    projection["nodes"] = []
    projection["edges"] = []
    page = _page(tmp_path, projection)

    assert "No live worker evidence yet." in page
    assert "Empty regions are unknown, not idle." in page
    assert "No directed activity has been observed yet." in page


def test_agent_brain_is_responsive_reduced_motion_and_reuses_sse_state(tmp_path) -> None:
    page = _page(tmp_path, _projection())

    assert "@media (max-width:560px)" in page
    assert "grid-template-areas:&quot;" not in page
    assert "@media (prefers-reduced-motion:no-preference)" in page
    assert "@media (prefers-reduced-motion:reduce)" in page
    assert ".brain-edge{animation:none!important}" in page
    assert DASHBOARD_JS.count("document.addEventListener('click'") == 1
    assert "selectedBrainKey" in DASHBOARD_JS
    assert "showBrainDetail(selected)" in DASHBOARD_JS
    assert "current.replaceWith(replacement)" in DASHBOARD_JS
    assert "location.reload" not in DASHBOARD_JS
