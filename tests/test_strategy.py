from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from codex_local_harness.read_model import RunView
from codex_local_harness.strategy import (
    MAX_ANNOTATIONS,
    MAX_RUNS,
    compare_runs,
    derive_strategy,
    read_annotations,
)


def _view(run_id: str, **changes) -> RunView:
    base = RunView(
        run_id=run_id, issue=107, role="coder", model="model-a", profile="route-a",
        task_class="bounded", claimed_status="success", claimed_outcome="unknown",
        delivery_ready=True, lifecycle_state="completed", lifecycle_exit_status=0,
        lifecycle_dirty_entries=0, observed_outcome="passed", effective_outcome="passed",
        adjudicated=True, contradiction=False, failure_class=None, correction_of=None,
        correction_cycle=0, intervention_depth="none", codex_decision=None,
        evidence_refs=(f"evidence:{run_id}",), wall_seconds=10, local_tokens=100,
        frontier_tokens=0, turns=2, tool_calls=2, tool_errors=0,
    )
    return replace(base, **changes)


def test_comparison_aligns_stages_and_marks_first_observable_divergence() -> None:
    left = _view("left")
    right = _view("right", model="model-b", effective_outcome="failed", observed_outcome="failed")

    comparison = compare_runs((left, right), ("left", "right"))

    assert comparison["state"] == "compared"
    assert comparison["first_divergence"] == "identity"
    assert [item["stage"] for item in comparison["stages"]] == [
        "identity", "delivery", "lifecycle", "adjudication", "correction",
    ]
    assert comparison["stages"][3]["diverges"] is True


def test_comparison_reports_missing_and_unobserved_data_honestly() -> None:
    unknown = _view(
        "unknown", role=None, model=None, task_class=None, claimed_status=None,
        delivery_ready=None, lifecycle_state="unknown", adjudicated=False,
        effective_outcome="unadjudicated", observed_outcome="unknown",
    )
    assert compare_runs((unknown,), ("unknown", "missing"))["state"] == "incomparable"
    same = replace(unknown, run_id="same")
    result = compare_runs((unknown, same), ("unknown", "same"))
    assert result["state"] == "incomparable"
    assert result["first_divergence"] is None


def test_cohort_metrics_keep_denominators_and_exclusions_separate() -> None:
    views = (
        _view("observed"),
        _view(
            "missing", delivery_ready=None, adjudicated=False, effective_outcome="unadjudicated",
            observed_outcome="unknown", wall_seconds=0, claimed_status=None, tool_calls=0,
        ),
    )
    strategy = derive_strategy(views, {"nodes": [], "signals": []}, 0, 1)
    cohort = strategy["derivations"]["cohorts"][0]

    assert cohort["sample_warning"] == "insufficient sample"
    assert cohort["metrics"]["delivery"] == {
        "value": 1.0, "numerator": 1, "denominator": 1, "exclusions": 1,
        "source": "run-health delivery_ready",
    }
    assert cohort["metrics"]["latency_seconds"]["denominator"] == 1
    assert cohort["metrics"]["tool_errors"]["denominator"] == 1
    assert "score" not in cohort


def test_bottlenecks_are_counts_with_sources_not_causal_claims() -> None:
    live = {
        "nodes": [
            {"kind": "worker", "state": "waiting-inference"},
            {"kind": "worker", "state": "running-tests"},
        ],
        "signals": [{"kind": "silent-interval"}],
    }
    strategy = derive_strategy((_view("one", correction_cycle=2),), live, 3, 4)
    counts = {item["kind"]: item["count"] for item in strategy["derivations"]["bottlenecks"]}

    assert counts == {
        "inference-wait": 1, "tool-wait": 0, "tests": 1, "idle-gap": 1,
        "correction-loop": 2, "publication": 3, "unavailable-telemetry": 4,
    }
    assert strategy["annotation_policy"] == "read-only durable records; correlation is not causation"


def test_annotations_are_bounded_normalized_and_reject_malformed(tmp_path: Path) -> None:
    path = tmp_path / "annotations.jsonl"
    records = [
        {
            "annotation_id": f"a-{index}", "kind": "hypothesis",
            "summary": f"summary {index}", "run_ids": ["run-1"],
            "evidence_refs": ["evidence:1"], "source": "operator",
        }
        for index in range(MAX_ANNOTATIONS + 2)
    ]
    path.write_text("\n".join(json.dumps(item) for item in records), encoding="utf-8")
    assert len(read_annotations(path)) == MAX_ANNOTATIONS
    assert read_annotations(path)[0]["annotation_id"] == "a-2"
    path.write_text("not-json\n", encoding="utf-8")
    try:
        read_annotations(path)
    except ValueError as exc:
        assert "line 1" in str(exc)
    else:
        raise AssertionError("malformed annotation should fail locally")


def test_strategy_history_is_bounded_and_exclusions_are_reported() -> None:
    views = tuple(_view(f"run-{index:04}") for index in range(MAX_RUNS + 3))

    strategy = derive_strategy(views, {"nodes": [], "signals": []}, 0, 0)

    assert strategy["facts"]["runs"] == MAX_RUNS
    assert strategy["facts"]["run_history_exclusions"] == 3
    assert strategy["limits"]["runs"] == MAX_RUNS
