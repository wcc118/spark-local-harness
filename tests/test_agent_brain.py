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


def test_execution_microscope_and_chronological_rail_are_explicit(tmp_path) -> None:
    projection = _projection()
    projection["nodes"][0].update({
        "run_id": "run-1", "role": "coder", "model": "Qwen",
        "repository": "/work/example", "issue": 106, "session_id": "session-a",
        "phase": "waiting-inference", "elapsed_seconds": 12.5, "tool_calls": 3,
        "test_observations": 1, "files_observed": ["src/safe.py"],
        "branch": "issue-106", "commit": "a" * 40, "token_usage": None,
    })
    projection["events"] = [{
        "kind": "step-start", "run_id": "run-1", "status": "waiting-inference",
        "observed_at": "2026-08-22T12:00:00+00:00", "source_ref": "v5-log:run-1:1",
        "reason": "normalized event",
    }]
    projection["handoffs"] = [{"from": "coder", "to": "codex", "reason": "result retained"}]
    projection["signals"] = [{"kind": "repeated-tool", "reason": "observed twice; no failure inferred"}]
    projection["criteria_states"] = [{
        "criterion": "semantic acceptance", "state": "unknown", "reason": "no criterion evidence supplied",
    }]

    page = _page(tmp_path, projection)

    assert "Chronological evidence rail" in page
    assert page.index("2026-08-22T12:00:00+00:00") < page.index("Evidence handoffs")
    assert "coder → codex" in page
    assert "no failure inferred" in page
    assert "semantic acceptance" in page
    assert "File activity alone never verifies semantic completion." in page
    for field in (
        "brain-detail-run", "brain-detail-role", "brain-detail-objective",
        "brain-detail-files", "brain-detail-durable",
    ):
        assert f'id="{field}"' in page
        assert f"'{field}'" in DASHBOARD_JS


def test_microscope_selects_attention_runs_and_uses_bounded_criterion_states(tmp_path) -> None:
    snapshot = fixture_source(tmp_path).snapshot()
    page = render_dashboard(snapshot)

    assert snapshot["live_execution"]["criteria_states"] == [{
        "criterion": "run run-hidden outcome",
        "state": "contradicted",
        "reason": "worker claim conflicts with independent observation",
        "run_id": "run-hidden",
    }]
    assert 'data-kind="attention"' in page
    assert 'data-kind="run"' in page
    assert "Inspect" in page
    assert "scrollIntoView({block: 'nearest'})" in DASHBOARD_JS


def test_strategy_lab_separates_derivations_denominators_and_annotations(tmp_path) -> None:
    snapshot = fixture_source(tmp_path).snapshot(comparison=("run-hidden", "missing"))
    snapshot["strategy"]["facts"]["annotations"] = [{
        "kind": "hypothesis", "summary": "<b>route may be slow</b>",
        "run_ids": ["run-hidden"], "evidence_refs": ["evidence:one"], "source": "operator",
    }]
    page = render_dashboard(snapshot)

    assert "Evidence-backed organization diagnostics" in page
    assert "No synthetic score · no automatic tuning" in page
    assert "numerator, denominator, exclusions, and source" in page
    assert "insufficient sample" in page
    assert "selected run is absent from the current cohort" in page
    assert "Facts and deterministic derivations above are computed" in page
    assert "&lt;b&gt;route may be slow&lt;/b&gt;" in page
    assert "<b>route may be slow</b>" not in page


def test_live_execution_graph_is_accessible_deterministic_and_selectable(tmp_path) -> None:
    projection = _projection()
    snapshot = fixture_source(tmp_path).snapshot()
    snapshot["live_execution"] = projection
    first = render_dashboard(snapshot)
    second = render_dashboard(snapshot)
    graph_first = first.split('<section class="execution-graph-shell"', 1)[1].split("</section>", 1)[0]
    graph_second = second.split('<section class="execution-graph-shell"', 1)[1].split("</section>", 1)[0]
    assert graph_first == graph_second
    assert '<svg class="execution-graph"' in first
    assert 'aria-labelledby="execution-graph-svg-title execution-graph-svg-desc"' in first
    assert 'class="graph-node state-waiting-inference" role="button" tabindex="0"' in first
    assert 'class="graph-edge state-waiting-inference" role="button" tabindex="0"' in first
    assert 'marker-end="url(#graph-arrow)"' in first
    assert "event.key === 'Enter' || event.key === ' '" in DASHBOARD_JS


def test_graph_temporal_edges_are_dashed_and_text_paths_are_disclosed(tmp_path) -> None:
    projection = _projection()
    projection["nodes"].append({
        "id": "resource:dgx-spark", "kind": "resource", "label": "DGX Spark resources",
        "state": "active", "observed_at": "2026-08-22T12:00:00+00:00",
        "source_ref": "host-metrics", "reason": "host sample",
    })
    projection["edges"].append({
        "source": "model:qwen", "target": "resource:dgx-spark", "kind": "compute",
        "state": "active", "observed_at": "2026-08-22T12:00:00+00:00",
        "source_ref": "host-metrics:temporal-association",
        "reason": "per-process attribution is not inferred",
    })
    page = _page(tmp_path, projection)
    assert "graph-edge-temporal" in page
    assert "temporal association" in page
    assert "Evidence details · regions and textual signal paths" in page
    assert page.index("Live execution graph") < page.index("Observed signal paths")
    assert "per-process attribution is not inferred" in page


def test_graph_empty_state_mobile_and_reduced_motion_are_explicit(tmp_path) -> None:
    projection = _projection(source_state="missing")
    projection["nodes"] = []
    projection["edges"] = []
    page = _page(tmp_path, projection)
    assert "No execution nodes observed." in page
    assert "overflow:auto" in page
    assert "prefers-reduced-motion:reduce" in page
    assert ".graph-edge-line{animation:none!important}" in page
