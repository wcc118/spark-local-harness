from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from .read_model import RunView


MAX_COHORTS = 32
MAX_ANNOTATIONS = 64
MAX_RUNS = 256
COMPARISON_STAGES = ("identity", "delivery", "lifecycle", "adjudication", "correction")


def read_annotations(path: Path) -> tuple[dict[str, Any], ...]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid annotation at line {line_number}: {exc}") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("run_ids"), list):
            raise ValueError(f"invalid annotation at line {line_number}")
        records.append({
            "annotation_id": str(raw.get("annotation_id") or f"line-{line_number}")[:128],
            "kind": str(raw.get("kind") or "annotation")[:48],
            "summary": str(raw.get("summary") or "")[:1000],
            "run_ids": [str(item)[:128] for item in raw["run_ids"][:16]],
            "evidence_refs": [str(item)[:256] for item in raw.get("evidence_refs", [])[:24]],
            "observed_at": str(raw.get("observed_at") or "")[:64],
            "source": str(raw.get("source") or "operator")[:64],
        })
    return tuple(records[-MAX_ANNOTATIONS:])


def _metric(value: float | int | None, numerator: int | float, denominator: int, exclusions: int, source: str) -> dict[str, Any]:
    return {
        "value": value, "numerator": numerator, "denominator": denominator,
        "exclusions": exclusions, "source": source,
    }


def _cohort(items: list[RunView], key: tuple[str, str, str]) -> dict[str, Any]:
    total = len(items)
    delivery_known = [item for item in items if item.delivery_ready is not None]
    adjudicated = [item for item in items if item.adjudicated]
    latency_known = [item.wall_seconds for item in items if item.wall_seconds > 0]
    token_known = [item.local_tokens + item.frontier_tokens for item in items if item.claimed_status is not None]
    tool_observed = [item for item in items if item.tool_calls > 0]
    infrastructure = [
        item for item in items
        if item.lifecycle_state in {"interrupted", "cancelled", "timed-out"}
        or (item.failure_class and "infrastructure" in item.failure_class.lower())
    ]
    return {
        "model": key[0], "route": key[1], "task_class": key[2], "runs": total,
        "sample_warning": "insufficient sample" if total < 3 else None,
        "metrics": {
            "delivery": _metric(
                sum(item.delivery_ready is True for item in delivery_known) / len(delivery_known) if delivery_known else None,
                sum(item.delivery_ready is True for item in delivery_known), len(delivery_known), total - len(delivery_known),
                "run-health delivery_ready",
            ),
            "adjudication": _metric(
                sum(item.effective_outcome == "passed" for item in adjudicated) / len(adjudicated) if adjudicated else None,
                sum(item.effective_outcome == "passed" for item in adjudicated), len(adjudicated), total - len(adjudicated),
                "latest independent adjudication",
            ),
            "corrections": _metric(sum(item.correction_cycle for item in items), sum(item.correction_cycle for item in items), total, 0, "run/adjudication correction_cycle"),
            "latency_seconds": _metric(mean(latency_known) if latency_known else None, sum(latency_known), len(latency_known), total - len(latency_known), "observed run wall_seconds"),
            "tokens": _metric(mean(token_known) if token_known else None, sum(token_known), len(token_known), total - len(token_known), "recorded local + frontier tokens"),
            "tool_errors": _metric(sum(item.tool_errors for item in tool_observed), sum(item.tool_errors for item in tool_observed), len(tool_observed), total - len(tool_observed), "runs with observable tool calls"),
            "infrastructure_failure": _metric(len(infrastructure) / total if total else None, len(infrastructure), total, 0, "terminal lifecycle and failure class"),
        },
    }


def _stage(view: RunView, stage: str) -> dict[str, Any]:
    if stage == "identity":
        value = f"{view.role or 'unknown'} / {view.model or 'unknown'} / {view.task_class or 'unknown'}"
        observed = any((view.role, view.model, view.task_class))
    elif stage == "delivery":
        value = f"{view.claimed_status or 'unknown'} / ready={view.delivery_ready}"
        observed = view.claimed_status is not None or view.delivery_ready is not None
    elif stage == "lifecycle":
        value = view.lifecycle_state
        observed = view.lifecycle_state != "unknown"
    elif stage == "adjudication":
        value = view.effective_outcome
        observed = view.adjudicated
    else:
        value = f"cycle {view.correction_cycle} / intervention {view.intervention_depth}"
        observed = view.correction_cycle > 0 or view.intervention_depth != "none"
    return {"stage": stage, "value": value, "observed": observed}


def compare_runs(views: Iterable[RunView], run_ids: tuple[str, str] | None) -> dict[str, Any]:
    if not run_ids or not all(run_ids) or run_ids[0] == run_ids[1]:
        return {"state": "selection-required", "run_ids": list(run_ids or ()), "stages": [], "first_divergence": None}
    by_id = {view.run_id: view for view in views}
    if any(run_id not in by_id for run_id in run_ids):
        return {"state": "incomparable", "run_ids": list(run_ids), "stages": [], "first_divergence": None, "reason": "selected run is absent from the current cohort"}
    aligned = []
    first = None
    for stage in COMPARISON_STAGES:
        left, right = _stage(by_id[run_ids[0]], stage), _stage(by_id[run_ids[1]], stage)
        comparable = left["observed"] and right["observed"]
        diverges = comparable and left["value"] != right["value"]
        if diverges and first is None:
            first = stage
        aligned.append({"stage": stage, "left": left, "right": right, "comparable": comparable, "diverges": diverges})
    return {
        "state": "compared" if any(item["comparable"] for item in aligned) else "incomparable",
        "run_ids": list(run_ids), "stages": aligned, "first_divergence": first,
        "reason": None if any(item["comparable"] for item in aligned) else "no aligned stage has evidence on both runs",
    }


def derive_strategy(
    views: Iterable[RunView], live: dict[str, Any], publication_failures: int,
    unavailable_sources: int, annotations: Iterable[dict[str, Any]] = (),
    comparison: tuple[str, str] | None = None,
) -> dict[str, Any]:
    all_items = tuple(views)
    items = all_items[-MAX_RUNS:]
    grouped: dict[tuple[str, str, str], list[RunView]] = {}
    for view in items:
        key = (view.model or "unknown", view.profile or "unknown", view.task_class or "unknown")
        grouped.setdefault(key, []).append(view)
    workers = [node for node in live.get("nodes", []) if node.get("kind") == "worker"]
    states = [node.get("state") for node in workers]
    signal_kinds = [item.get("kind") for item in live.get("signals", [])]
    bottlenecks = [
        {"kind": "inference-wait", "count": states.count("waiting-inference"), "source": "live worker state"},
        {"kind": "tool-wait", "count": states.count("waiting-tool"), "source": "live worker state"},
        {"kind": "tests", "count": states.count("running-tests"), "source": "live worker state"},
        {"kind": "idle-gap", "count": signal_kinds.count("silent-interval"), "source": "normalized event intervals ≥120s"},
        {"kind": "correction-loop", "count": sum(item.correction_cycle for item in items), "source": "run/adjudication records"},
        {"kind": "publication", "count": publication_failures, "source": "publication failure ledger"},
        {"kind": "unavailable-telemetry", "count": unavailable_sources, "source": "configured source states"},
    ]
    return {
        "schema_version": 1,
        "facts": {
            "runs": len(items), "run_history_exclusions": max(0, len(all_items) - len(items)),
            "annotations": list(annotations)[:MAX_ANNOTATIONS],
        },
        "derivations": {
            "bottlenecks": bottlenecks,
            "cohorts": [_cohort(grouped[key], key) for key in sorted(grouped)[:MAX_COHORTS]],
            "comparison": compare_runs(items, comparison),
        },
        "annotation_policy": "read-only durable records; correlation is not causation",
        "limits": {"runs": MAX_RUNS, "cohorts": MAX_COHORTS, "annotations": MAX_ANNOTATIONS},
    }
