from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

from .diagnostics import Adjudication, LifecycleEvent, latest_adjudications
from .health import RunHealth


TERMINAL_EVENTS = {"completed", "interrupted", "cancelled", "timed-out"}


@dataclass(frozen=True)
class RunView:
    run_id: str
    issue: int | None
    role: str | None
    model: str | None
    profile: str | None
    task_class: str | None
    claimed_status: str | None
    claimed_outcome: str | None
    delivery_ready: bool | None
    lifecycle_state: str
    observed_outcome: str
    effective_outcome: str
    adjudicated: bool
    contradiction: bool
    failure_class: str | None
    correction_of: str | None
    correction_cycle: int
    intervention_depth: str
    codex_decision: str | None
    evidence_refs: tuple[str, ...]
    wall_seconds: float
    local_tokens: int
    frontier_tokens: int
    turns: int
    tool_calls: int
    tool_errors: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_run_views(
    runs: Iterable[RunHealth],
    adjudications: Iterable[Adjudication] = (),
    lifecycle: Iterable[LifecycleEvent] = (),
) -> tuple[RunView, ...]:
    run_by_id = {run.run_id: run for run in runs}
    adjudication_by_id = latest_adjudications(adjudications)
    lifecycle_by_id: dict[str, list[LifecycleEvent]] = {}
    for event in lifecycle:
        lifecycle_by_id.setdefault(event.run_id, []).append(event)
    run_ids = set(run_by_id) | set(adjudication_by_id) | set(lifecycle_by_id)
    views = [
        _build_view(run_id, run_by_id.get(run_id), adjudication_by_id.get(run_id), lifecycle_by_id.get(run_id, []))
        for run_id in run_ids
    ]
    return tuple(sorted(views, key=lambda view: view.run_id))


def summarize_views(views: Iterable[RunView]) -> dict[str, Any]:
    items = tuple(views)
    adjudicated = tuple(view for view in items if view.adjudicated)
    confirmed_autonomous = sum(
        view.effective_outcome == "passed"
        and view.intervention_depth == "none"
        and view.frontier_tokens == 0
        for view in adjudicated
    )
    return {
        "runs": len(items),
        "adjudicated_runs": len(adjudicated),
        "unadjudicated_runs": sum(not view.adjudicated for view in items),
        "contradictions": sum(view.contradiction for view in items),
        "interrupted_runs": sum(view.lifecycle_state == "interrupted" for view in items),
        "cancelled_runs": sum(view.lifecycle_state == "cancelled" for view in items),
        "timed_out_runs": sum(view.lifecycle_state == "timed-out" for view in items),
        "effective_outcome_counts": _counts(view.effective_outcome for view in items),
        "failure_counts": _counts(view.failure_class for view in items if view.failure_class),
        "confirmed_local_autonomy_rate": confirmed_autonomous / len(adjudicated) if adjudicated else None,
        "local_tokens": sum(view.local_tokens for view in items),
        "frontier_tokens": sum(view.frontier_tokens for view in items),
        "correction_cycles": sum(view.correction_cycle for view in items),
        "codex_interventions": sum(view.intervention_depth != "none" for view in items),
    }


def _build_view(run_id: str, run: RunHealth | None, adjudication: Adjudication | None, events: list[LifecycleEvent]) -> RunView:
    terminal = _terminal_event(events)
    lifecycle_state = terminal.event if terminal else ("started" if events else "unknown")
    if adjudication:
        observed = effective = adjudication.verdict
    elif lifecycle_state in {"interrupted", "cancelled", "timed-out"}:
        observed = effective = lifecycle_state
    else:
        observed, effective = "unknown", "unadjudicated"
    claimed_success = bool(run and run.status == "success" and run.delivery_ready is not False)
    contradiction = claimed_success and effective in {"failed", "interrupted", "cancelled", "timed-out"}
    wall_seconds = run.wall_seconds if run else (terminal.wall_seconds if terminal and terminal.wall_seconds else 0.0)
    return RunView(
        run_id=run_id,
        issue=run.issue if run else None,
        role=run.role if run else None,
        model=run.model if run else None,
        profile=run.profile if run else None,
        task_class=run.task_class if run else None,
        claimed_status=run.status if run else None,
        claimed_outcome=run.outcome if run else None,
        delivery_ready=run.delivery_ready if run else None,
        lifecycle_state=lifecycle_state,
        observed_outcome=observed,
        effective_outcome=effective,
        adjudicated=adjudication is not None,
        contradiction=contradiction,
        failure_class=adjudication.failure_class if adjudication else (run.failure_class if run else None),
        correction_of=adjudication.correction_of if adjudication else None,
        correction_cycle=adjudication.correction_cycle if adjudication else (run.correction_cycle if run else 0),
        intervention_depth=adjudication.intervention_depth if adjudication else (run.intervention_depth if run else "none"),
        codex_decision=adjudication.codex_decision if adjudication else (run.codex_decision if run else None),
        evidence_refs=adjudication.evidence_refs if adjudication else (),
        wall_seconds=wall_seconds,
        local_tokens=run.usage.local_total if run else 0,
        frontier_tokens=run.usage.frontier_total if run else 0,
        turns=run.turns if run else 0,
        tool_calls=run.tool_calls if run else 0,
        tool_errors=run.tool_errors if run else 0,
    )


def _terminal_event(events: Iterable[LifecycleEvent]) -> LifecycleEvent | None:
    terminal = [event for event in events if event.event in TERMINAL_EVENTS]
    return max(terminal, key=lambda event: (event.observed_at, event.event_id)) if terminal else None


def _counts(values: Iterable[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))
