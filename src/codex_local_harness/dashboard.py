from __future__ import annotations

import argparse
import html
import json
import re
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit

from .benchmark import BenchmarkAttempt, BenchmarkManifest, PairedSummary, BenchmarkMetrics
from .control_plane import ControlBroker, ControlError
from .diagnostics import Adjudication, LifecycleEvent, PublicationFailure, read_records
from .frontier import FrontierStatus, latest_status
from .health import read_jsonl
from .live_execution import live_fingerprint, project_live_execution
from .operation_usage import OperationUsageSnapshot, derive_usage
from .plan_event import derive_plans, read_plan_records
from .read_model import RunView, build_run_views, summarize_views
from .strategy import derive_strategy, read_annotations
from .workflow import derive_operations, read_workflow_records


@dataclass(frozen=True)
class OperationalVerdict:
    """Deterministic read-only operational status projection."""
    severity: str  # "critical", "degraded", "no-data", "healthy"
    headline: str
    reasons: tuple[str, ...]
    source_references: tuple[str, ...]
    detail_counts: dict[str, int]
    oldest_unadjudicated_at: str | None = None


@dataclass(frozen=True)
class AttentionItem:
    """A single item requiring operator attention, ordered by severity then recency."""
    severity: str  # "critical", "degraded", "warning"
    subject: str  # human-readable subject
    reason: str  # why it needs attention
    issue: int | None  # GitHub issue if available
    repository: str | None
    occurred_at: str  # ISO timestamp for recency sorting


def _compute_operational_verdict(
    views: tuple[RunView, ...], operations: list[dict[str, Any]],
    publication_failures: tuple[PublicationFailure, ...], sources: list[dict[str, Any]],
    now: datetime, run_observed_at: dict[str, str],
) -> OperationalVerdict:
    """Derive status from immutable evidence without inferring success."""
    unadjudicated = [view for view in views if view.effective_outcome == "unadjudicated"]
    contradictions = [view for view in views if view.contradiction]
    interrupted = [view for view in views if view.lifecycle_state in {"interrupted", "cancelled", "timed-out"}]
    publication_failed = len(publication_failures)
    source_problems = sum(1 for s in sources if s["state"] != "ok")
    failed_ops = [op for op in operations if op["current_state"] == "failed"]
    blocked_ops = [op for op in operations if op["current_state"] == "blocked"]
    stale_sources = [
        source for source in sources
        if source.get("state") == "ok" and source.get("mtime_epoch") is not None
        and now.timestamp() - source["mtime_epoch"] > 1800
    ]
    has_records = bool(
        views or operations or publication_failures
        or any(source.get("records", 0) for source in sources)
    )
    critical_count = len(contradictions) + len(interrupted) + len(failed_ops) + len(blocked_ops)
    degraded_count = publication_failed + source_problems + len(stale_sources) + len(unadjudicated)
    if not has_records:
        severity = "no-data"
        headline = "No diagnostic data"
    elif critical_count:
        severity = "critical"
        headline = (
            "1 critical condition requires attention" if critical_count == 1
            else f"{critical_count} critical conditions require attention"
        )
    elif degraded_count:
        severity = "degraded"
        headline = (
            "1 diagnostic condition needs review" if degraded_count == 1
            else f"{degraded_count} diagnostic conditions need review"
        )
    else:
        severity = "healthy"
        headline = "System healthy"

    reasons = []
    if contradictions:
        reasons.append(f"{len(contradictions)} contradiction(s) between claim and observation")
    if failed_ops:
        reasons.append(f"{len(failed_ops)} failed workflow operation(s)")
    if blocked_ops:
        reasons.append(f"{len(blocked_ops)} blocked workflow operation(s)")
    if interrupted:
        reasons.append(f"{len(interrupted)} interrupted/cancelled/timed-out run(s)")
    if publication_failed:
        reasons.append(f"{publication_failed} telemetry publication failure(s)")
    if source_problems:
        reasons.append(f"{source_problems} missing or malformed source file(s)")
    if stale_sources:
        reasons.append(f"{len(stale_sources)} source file(s) older than 30 minutes")
    if unadjudicated:
        reasons.append(f"{len(unadjudicated)} unadjudicated run(s)")

    source_refs = tuple(s["name"] for s in sources if s["state"] != "ok")
    unadjudicated_times = [
        run_observed_at[view.run_id] for view in unadjudicated
        if view.run_id in run_observed_at
    ]
    oldest_unadjudicated = min(unadjudicated_times) if unadjudicated_times else None

    return OperationalVerdict(
        severity=severity,
        headline=headline,
        reasons=reasons,
        source_references=source_refs,
        detail_counts={
            "unadjudicated": len(unadjudicated),
            "contradictions": len(contradictions),
            "interrupted": len(interrupted),
            "publication_failures": publication_failed,
            "source_problems": source_problems,
            "failed_operations": len(failed_ops),
            "blocked_operations": len(blocked_ops),
        },
        oldest_unadjudicated_at=oldest_unadjudicated,
    )


def _build_attention_queue(
    views: tuple[RunView, ...], operations: list[dict[str, Any]],
    publication_failures: tuple[PublicationFailure, ...], sources: list[dict[str, Any]],
    now: datetime, run_observed_at: dict[str, str],
) -> tuple[tuple[AttentionItem, ...], int]:
    """Return the five most urgent distinct incidents and the full count."""
    items: list[AttentionItem] = []
    run_identity: dict[str, tuple[str | None, int | None]] = {}
    for operation in operations:
        repository = operation.get("repository")
        if not isinstance(repository, str) or not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
            repository = None
        for run_id in operation.get("run_ids", []):
            run_identity[run_id] = (repository, operation.get("issue"))

    for view in views:
        reasons = []
        severity = "warning"
        if view.contradiction:
            severity = "critical"
            reasons.append(f"claim '{view.claimed_status}' vs observed '{view.effective_outcome}'")
        if view.lifecycle_state in {"interrupted", "cancelled", "timed-out"}:
            severity = "critical"
            reasons.append(f"lifecycle state: {view.lifecycle_state}")
        elif view.effective_outcome == "unadjudicated":
            reasons.append("independent adjudication not observed")
        if reasons:
            repository, operation_issue = run_identity.get(view.run_id, (None, None))
            items.append(AttentionItem(
                severity, f"Run {view.run_id[:12]}", "; ".join(reasons),
                view.issue or operation_issue, repository, run_observed_at.get(view.run_id, ""),
            ))

    for operation in operations:
        state = operation.get("current_state")
        if state not in {"failed", "blocked"}:
            continue
        repository = operation.get("repository")
        if not isinstance(repository, str) or not re.fullmatch(r"[^/\s]+/[^/\s]+", repository):
            repository = None
        items.append(AttentionItem(
            "critical", f"Operation {str(operation.get('operation_id', 'unknown'))[:12]}",
            f"workflow state: {state}", operation.get("issue"), repository,
            operation.get("updated_at") or "",
        ))

    for failure in publication_failures:
        items.append(AttentionItem(
            "degraded", f"Publication failure: {failure.run_id[:12]}",
            f"{failure.phase} → {failure.target} ({failure.error_class})",
            None, None, failure.observed_at,
        ))

    for source in sources:
        if source["state"] != "ok":
            items.append(AttentionItem(
                "degraded", f"Source {source['name']}", f"source state: {source['state']}",
                None, None, "",
            ))
        elif source.get("mtime_epoch") is not None and now.timestamp() - source["mtime_epoch"] > 1800:
            occurred_at = datetime.fromtimestamp(source["mtime_epoch"], timezone.utc).isoformat()
            items.append(AttentionItem(
                "degraded", f"Source {source['name']}", "telemetry older than 30 minutes",
                None, None, occurred_at,
            ))

    severity_order = {"critical": 0, "degraded": 1, "warning": 2}
    ordered = []
    for severity in ("critical", "degraded", "warning"):
        ordered.extend(sorted(
            (item for item in items if item.severity == severity),
            key=lambda item: (item.occurred_at, item.subject), reverse=True,
        ))
    ordered.extend(item for item in items if item.severity not in severity_order)
    return tuple(ordered[:5]), len(ordered)


@dataclass(frozen=True)
class DashboardSource:
    runs: Path
    adjudications: Path
    lifecycle: Path
    workflow: Path | None = None
    frontier: Path | None = None
    publication_failures: Path | None = None
    release: str = "v0.2.0"
    mode: str = "live"
    now: datetime | None = None
    operation_usage: Path | None = None
    plans: Path | None = None
    v5_runs: Path | None = None
    annotations: Path | None = None
    benchmark_manifest: Path | None = None
    benchmark_attempts: Path | None = None
    benchmark_summaries: Path | None = None

    def _current_time(self) -> datetime:
        return self.now if self.now is not None else datetime.now(timezone.utc)

    def snapshot(
        self, session_id: str | None = None, comparison: tuple[str, str] | None = None,
    ) -> dict[str, Any]:
        statuses: list[dict[str, Any]] = []
        runs = self._load(self.runs, "runs", read_jsonl, statuses)
        adjudications = self._load(self.adjudications, "adjudications", lambda path: read_records(path, Adjudication), statuses)
        lifecycle = self._load(self.lifecycle, "lifecycle", lambda path: read_records(path, LifecycleEvent), statuses)
        workflow = self._load(self.workflow, "workflow", read_workflow_records, statuses) if self.workflow else ()
        frontier_records = self._load(self.frontier, "frontier", lambda path: read_records(path, FrontierStatus), statuses) if self.frontier else ()
        publication_failures = self._load(
            self.publication_failures,
            "publication-failures",
            lambda path: read_records(path, PublicationFailure),
            statuses,
        ) if self.publication_failures else ()
        usage_snapshots = self._load(
            self.operation_usage, "operation-usage",
            lambda path: read_records(path, OperationUsageSnapshot), statuses,
        ) if self.operation_usage else ()
        plan_events = self._load(self.plans, "plans", read_plan_records, statuses) if self.plans else ()
        annotations = self._load(
            self.annotations, "annotations", read_annotations, statuses,
        ) if self.annotations and self.annotations.exists() else ()
        benchmark_manifest = self._load_benchmark_manifest(statuses) if self.benchmark_manifest else None
        benchmark_attempts = self._load_benchmark_attempts(statuses) if self.benchmark_attempts else ()
        benchmark_summaries = self._load_benchmark_summaries(statuses) if self.benchmark_summaries else ()
        views = build_run_views(
            runs, adjudications, lifecycle,
        )
        frontier = latest_status(frontier_records)
        operations = derive_operations(workflow)
        plans = derive_plans(plan_events, now=self._current_time())
        current_operations = [operation for operation in operations if operation["current_state"] != "superseded"]
        sessions = _derive_sessions(current_operations, self._current_time(), plans)
        selected = session_id if session_id and any(item["session_id"] == session_id for item in sessions) else None
        selected_run_ids: set[str] | None = None
        if selected is not None:
            current_operations = [item for item in current_operations if (item["session_id"] or "legacy") == selected]
            selected_run_ids = {run_id for item in current_operations for run_id in item["run_ids"]}
            views = tuple(view for view in views if view.run_id in selected_run_ids)
            runs = tuple(item for item in runs if item.run_id in selected_run_ids)
            plans = tuple(item for item in plans if (item["session_id"] or "legacy") == selected)
        operation_usage = derive_usage(usage_snapshots)
        if selected is not None:
            operation_usage = tuple(item for item in operation_usage if item["session_id"] == selected)
        accounting = _accounting(runs, views, operation_usage, current_operations)

        current_time = self._current_time()
        scoped_failures = tuple(
            failure for failure in publication_failures
            if selected_run_ids is None or failure.run_id in selected_run_ids
        )
        run_observed_at: dict[str, str] = {}
        for record in (*adjudications, *lifecycle):
            if selected_run_ids is None or record.run_id in selected_run_ids:
                run_observed_at[record.run_id] = max(
                    run_observed_at.get(record.run_id, ""), record.observed_at,
                )
        verdict = _compute_operational_verdict(
            views, current_operations, scoped_failures, statuses, current_time, run_observed_at,
        )
        attention_queue, attention_total = _build_attention_queue(
            views, current_operations, scoped_failures, statuses, current_time, run_observed_at,
        )
        health_by_id = {run.run_id: run for run in runs}
        operation_by_run_id = {
            run_id: operation
            for operation in current_operations
            for run_id in operation["run_ids"]
        }
        run_evidence: dict[str, dict[str, Any]] = {}
        for view in views:
            health = health_by_id.get(view.run_id)
            operation = operation_by_run_id.get(view.run_id, {})
            terminal_state = None
            if view.lifecycle_state == "completed":
                terminal_state = (
                    "completed"
                    if view.lifecycle_exit_status in {None, 0}
                    and view.claimed_status != "failure"
                    else "failed"
                )
            elif view.lifecycle_state in {"interrupted", "cancelled", "timed-out"}:
                terminal_state = view.lifecycle_state
            run_evidence[view.run_id] = {
                "terminal_state": terminal_state,
                "source_ref": f"lifecycle:{view.run_id}:{view.lifecycle_state}",
                "role": view.role,
                "model": view.model,
                "repository": health.repository if health else operation.get("repository"),
                "issue": view.issue,
                "session_id": operation.get("session_id"),
                "elapsed_seconds": view.wall_seconds,
                "tool_calls": view.tool_calls,
                "pull_request": health.pull_request if health else operation.get("pull_request"),
                "token_usage": view.local_tokens + view.frontier_tokens,
            }
        live_execution = project_live_execution(
            self.v5_runs, now=current_time, session_id=selected, run_evidence=run_evidence,
        )
        criterion_states = []
        for view in views:
            if view.contradiction:
                criterion_state = "contradicted"
                criterion_reason = "worker claim conflicts with independent observation"
            elif view.effective_outcome == "unadjudicated":
                criterion_state = "unknown"
                criterion_reason = "independent criterion judgment is absent"
            elif view.effective_outcome in {"passed", "success"}:
                criterion_state = "verified"
                criterion_reason = "independent run outcome supports the recorded claim"
            else:
                criterion_state = "evidence-observed"
                criterion_reason = f'independent outcome observed: {view.effective_outcome}'
            criterion_states.append({
                "criterion": f"run {view.run_id} outcome", "state": criterion_state,
                "reason": criterion_reason, "run_id": view.run_id,
            })
        if criterion_states:
            live_execution["criteria_states"] = criterion_states[:64]
        strategy = derive_strategy(
            views, live_execution, len(scoped_failures),
            sum(status["state"] != "ok" for status in statuses)
            + (live_execution.get("source_state") != "ok"),
            annotations, comparison,
        )

        return {
            "schema_version": 1,
            "release": self.release,
            "read_only": True,
            "mode": self.mode,
            "freshness": self._freshness(statuses),
            "sources": statuses,
            "summary": summarize_views(views),
            "runs": [view.as_dict() for view in views],
            "operations": current_operations,
            "plans": plans,
            "sessions": sessions,
            "selected_session": selected,
            "operation_usage": operation_usage,
            "accounting": accounting,
            "superseded_operations": sum(operation["current_state"] == "superseded" for operation in operations),
            "publication_failures": [failure.as_dict() for failure in publication_failures],
            "codex": frontier.as_dict() if frontier else FrontierStatus(
                "unavailable", "1970-01-01T00:00:00Z", "unavailable", note_code="official-interface-unavailable"
            ).as_dict(),
            "operational_verdict": {
                "severity": verdict.severity,
                "headline": verdict.headline,
                "reasons": verdict.reasons,
                "source_references": verdict.source_references,
                "detail_counts": verdict.detail_counts,
                "oldest_unadjudicated_at": verdict.oldest_unadjudicated_at,
            },
            "attention_queue": [
                {
                    "severity": item.severity,
                    "subject": item.subject,
                    "reason": item.reason,
                    "issue": item.issue,
                    "repository": item.repository,
                    "occurred_at": item.occurred_at,
                }
                for item in attention_queue
            ],
            "attention_total": attention_total,
            "live_execution": live_execution,
            "strategy": strategy,
            "benchmark_manifest": benchmark_manifest.as_dict() if benchmark_manifest else None,
            "benchmark_attempts": [a.as_dict() for a in benchmark_attempts],
            "benchmark_summaries": [s.as_dict() for s in benchmark_summaries],
        }

    @staticmethod
    def _load(path: Path, name: str, reader: Any, statuses: list[dict[str, Any]]) -> tuple[Any, ...]:
        if not path.exists():
            statuses.append({"name": name, "state": "missing", "file": path.name})
            return ()
        try:
            records = reader(path)
            mtime_epoch = path.stat().st_mtime if records else None
        except (OSError, ValueError):
            statuses.append({"name": name, "state": "malformed", "file": path.name})
            return ()
        statuses.append({"name": name, "state": "ok", "file": path.name, "records": len(records), "mtime_epoch": mtime_epoch})
        return records

    def _load_benchmark_manifest(self, statuses: list[dict[str, Any]]) -> BenchmarkManifest | None:
        """Load benchmark manifest if present."""
        if not self.benchmark_manifest or not self.benchmark_manifest.exists():
            statuses.append({"name": "benchmark-manifest", "state": "missing", "file": "benchmark-manifest.json"})
            return None
        try:
            with self.benchmark_manifest.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return BenchmarkManifest.from_dict(data)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            statuses.append({"name": "benchmark-manifest", "state": "malformed", "file": "benchmark-manifest.json"})
            return None

    def _load_benchmark_attempts(self, statuses: list[dict[str, Any]]) -> tuple[BenchmarkAttempt, ...]:
        """Load benchmark attempts from JSONL if present."""
        if not self.benchmark_attempts or not self.benchmark_attempts.exists():
            statuses.append({"name": "benchmark-attempts", "state": "missing", "file": "benchmark-attempts.jsonl"})
            return ()
        try:
            attempts = []
            with self.benchmark_attempts.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    attempts.append(BenchmarkAttempt(**data))
            statuses.append({"name": "benchmark-attempts", "state": "ok", "file": "benchmark-attempts.jsonl", "records": len(attempts)})
            return tuple(attempts)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            statuses.append({"name": "benchmark-attempts", "state": "malformed", "file": "benchmark-attempts.jsonl"})
            return ()

    def _load_benchmark_summaries(self, statuses: list[dict[str, Any]]) -> tuple[PairedSummary, ...]:
        """Load benchmark paired summaries from JSONL if present."""
        if not self.benchmark_summaries or not self.benchmark_summaries.exists():
            statuses.append({"name": "benchmark-summaries", "state": "missing", "file": "benchmark-summaries.jsonl"})
            return ()
        try:
            summaries = []
            with self.benchmark_summaries.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    data = json.loads(line)
                    summaries.append(PairedSummary(**data))
            statuses.append({"name": "benchmark-summaries", "state": "ok", "file": "benchmark-summaries.jsonl", "records": len(summaries)})
            return tuple(summaries)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            statuses.append({"name": "benchmark-summaries", "state": "malformed", "file": "benchmark-summaries.jsonl"})
            return ()

    def paths(self) -> tuple[Path, ...]:
        return tuple(path for path in (
            self.runs,
            self.adjudications,
            self.lifecycle,
            self.workflow,
            self.frontier,
            self.publication_failures,
            self.operation_usage,
            self.plans,
            self.annotations,
            self.benchmark_manifest,
            self.benchmark_attempts,
            self.benchmark_summaries,
        ) if path is not None)

    def fingerprint(self) -> tuple[tuple[str, int, int], ...]:
        result = []
        for path in self.paths():
            try:
                stat = path.stat()
                result.append((str(path), stat.st_mtime_ns, stat.st_size))
            except FileNotFoundError:
                result.append((str(path), 0, 0))
        result.extend((f"v5:{name}", mtime, size) for name, mtime, size in live_fingerprint(self.v5_runs))
        return tuple(result)

    def _freshness(self, statuses: list[dict[str, Any]]) -> dict[str, Any]:
        mtimes = [status["mtime_epoch"] for status in statuses if status.get("mtime_epoch") is not None]
        newest_mtime_epoch = max(mtimes) if mtimes else None
        age_seconds = None if newest_mtime_epoch is None else max(
            0.0, self._current_time().timestamp() - newest_mtime_epoch
        )
        return {
            "source_files": len(self.paths()),
            "existing_files": sum(path.exists() for path in self.paths()),
            "newest_mtime_epoch": newest_mtime_epoch,
            "newest_age_seconds": age_seconds,
        }


def _format_age(seconds: float) -> str:
    """Format a non-negative age without implying false precision."""
    units = [
        ("year", 365.25 * 24 * 60 * 60),
        ("month", 30 * 24 * 60 * 60),
        ("day", 24 * 60 * 60),
        ("hour", 60 * 60),
        ("minute", 60),
        ("second", 1),
    ]

    for name, duration in units:
        if seconds >= duration:
            value = int(seconds / duration)
            suffix = "s" if value != 1 else ""
            return f"{value} {name}{suffix} ago"

    return "just now"


def _freshness_severity(age_seconds: float | None) -> str:
    """Classify data age so staleness can be escalated visually, independent of socket health."""
    if age_seconds is None:
        return "unknown"
    if age_seconds < 300:
        return "good"
    if age_seconds < 1800:
        return "warn"
    return "critical"


_SOURCE_HINTS = {
    "missing": "not created yet — no records have been written",
    "malformed": "unreadable — check the last line for truncated or invalid JSON",
}


def _triage_bucket(view: dict[str, Any]) -> str:
    if view["contradiction"]:
        return "contradiction"
    if view["effective_outcome"] == "unadjudicated":
        return "needs-judgment"
    return "clean"


def _render_operational_verdict(
    verdict: dict[str, Any], attention_queue: list[dict[str, Any]], attention_total: int,
) -> str:
    """Render the operational verdict and attention queue."""
    severity = verdict.get("severity", "unknown")
    headline = verdict.get("headline", "System status unknown")
    reasons = verdict.get("reasons", [])
    detail_counts = verdict.get("detail_counts", {})

    # Severity chip
    severity_class = {
        "critical": "critical",
        "degraded": "warn",
        "no-data": "unknown",
        "healthy": "good",
    }.get(severity, "unknown")
    severity_label = severity.upper()

    # Reasons list
    reasons_html = ""
    if reasons:
        reason_items = "".join(f'<li>{html.escape(reason)}</li>' for reason in reasons)
        reasons_html = f'<ul class="verdict-reasons">{reason_items}</ul>'

    # Detail counts
    detail_items = []
    for key, count in sorted(detail_counts.items()):
        if count > 0:
            label = key.replace("_", " ").title()
            detail_items.append(f'<span class="detail-count">{label}: {count}</span>')
    details_html = "".join(detail_items) if detail_items else '<span class="detail-count">All systems nominal</span>'

    visible_items = attention_queue
    omitted_count = max(0, attention_total - len(visible_items))

    queue_items = []
    for item in visible_items:
        item_severity = item.get("severity", "warning")
        item_sev_class = {
            "critical": "critical",
            "degraded": "warn",
            "warning": "warn",
        }.get(item_severity, "warn")

        # Build subject with GitHub link if available
        subject = html.escape(item.get("subject", "Unknown subject"))
        issue = item.get("issue")
        repo = item.get("repository")
        if issue and repo:
            url = f'https://github.com/{quote(repo, safe="/")}/issues/{issue}'
            subject = f'<a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">{subject}</a>'

        inspect_attrs = _microscope_attrs(
            key=f'attention:{item.get("subject")}:{item.get("occurred_at")}',
            label=item.get("subject", "Attention item"), kind="attention",
            state=item_severity, observed=item.get("occurred_at"), reason=item.get("reason"),
            source="operational-attention-queue", extra={"repository": repo, "issue": issue},
        )

        item_html = (
            f'<li class="attention-item {item_sev_class}">'
            f'<span class="attention-severity">{html.escape(item_severity.upper())}</span>'
            f'<span class="attention-subject">{subject}</span>'
            f'<span class="attention-reason">{html.escape(item.get("reason", ""))}</span>'
            f'<button type="button" class="microscope-trigger" {inspect_attrs}>Inspect</button>'
            f'</li>'
        )
        queue_items.append(item_html)

    queue_html = ""
    if queue_items:
        omitted_note = f' <span class="omitted-count">({omitted_count} more hidden)</span>' if omitted_count > 0 else ""
        queue_html = (
            f'<details class="attention-queue" open>'
            f'<summary class="attention-summary"><strong>Attention queue</strong>{omitted_note}</summary>'
            f'<ul class="attention-list">{"".join(queue_items)}</ul>'
            f'</details>'
        )

    # Main verdict container
    verdict_html = (
        f'<section class="operational-verdict severity-{severity_class}">'
        f'<h2>Operational verdict</h2>'
        f'<div class="verdict-header"><span class="verdict-severity">{severity_label}</span><strong>{html.escape(headline)}</strong></div>'
        f'<div class="verdict-details">{details_html}</div>'
        f'{reasons_html}'
        f'{queue_html}'
        f'</section>'
    )

    return verdict_html


_BRAIN_REGIONS = (
    ("intake", "Intake + repository", "Observed checkout and task boundary"),
    ("executive", "Codex executive", "Durable orchestration evidence"),
    ("routing", "Routing + inference", "Observed model endpoint activity"),
    ("action", "Coder + action", "Local worker execution"),
    ("verification", "Review + verification", "Reviewer and test evidence"),
    ("tools", "Tools", "Observable external actions"),
    ("memory", "Durable memory", "Issue, workflow, and retained result state"),
)
_BRAIN_STATES = {
    "active", "running", "running-tests", "waiting-inference", "waiting-tool",
    "stale", "completed", "failed", "interrupted", "unavailable", "unknown",
    "pending", "error",
}


def _brain_region(node: dict[str, Any]) -> str:
    kind = str(node.get("kind") or "unknown")
    role = str(node.get("role") or "").lower()
    if kind == "repository":
        return "intake"
    if kind in {"codex", "executive"}:
        return "executive"
    if kind in {"model", "router"}:
        return "routing"
    if kind == "test" or role == "reviewer":
        return "verification"
    if kind == "tool":
        return "tools"
    if kind in {"workflow", "issue", "memory"}:
        return "memory"
    return "action"


def _brain_state(value: Any) -> str:
    state = str(value or "unknown")
    return state if state in _BRAIN_STATES else "unknown"


def _brain_age(observed_at: Any, projection_time: Any) -> str:
    if not isinstance(observed_at, str):
        return "time unknown"
    try:
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        current = datetime.fromisoformat(str(projection_time).replace("Z", "+00:00"))
    except ValueError:
        return "time unknown"
    return _format_age(max(0.0, (current - observed).total_seconds()))


def _microscope_attrs(
    *, key: Any, label: Any, kind: Any, state: Any, observed: Any,
    reason: Any, source: Any, age: Any = "time unknown", extra: dict[str, Any] | None = None,
) -> str:
    values = {
        "brain-key": key, "label": label, "kind": kind, "state": _brain_state(state),
        "age": age, "observed": observed or "Observation time unavailable",
        "reason": reason or "Derivation reason unavailable",
        "source": source or "Evidence reference unavailable",
        **(extra or {}),
    }
    return " ".join(
        f'data-{name}="{html.escape(str(value) if value is not None else "Unavailable", quote=True)}"'
        for name, value in values.items()
    )


def _brain_button(item: dict[str, Any], index: int, projection_time: Any, *, edge: bool = False) -> str:
    state = _brain_state(item.get("state"))
    if edge:
        label = f'{item.get("source", "unknown")} → {item.get("target", "unknown")}'
        kind = str(item.get("kind") or "signal")
        css = "brain-edge"
        prefix = "brain-edge"
    else:
        label = str(item.get("label") or item.get("id") or "Unknown node")
        kind = str(item.get("kind") or "unknown")
        css = "brain-node"
        prefix = "brain-node"
    observed = str(item.get("observed_at") or "Observation time unavailable")
    reason = str(item.get("reason") or "Derivation reason unavailable")
    source = str(item.get("source_ref") or "Evidence reference unavailable")
    age = _brain_age(item.get("observed_at"), projection_time)
    selection_key = str(item.get("id") or f'{item.get("source")}:{item.get("target")}:{kind}')
    files = item.get("files_observed") if isinstance(item.get("files_observed"), list) else []
    durable = " · ".join(
        f"{name} {item.get(name)}" for name in ("branch", "commit", "pull_request") if item.get(name)
    )
    attributes = _microscope_attrs(
        key=selection_key, label=label, kind=kind, state=state, observed=observed,
        reason=reason, source=source, age=age,
        extra={
            "run": item.get("run_id"), "role": item.get("role"), "model": item.get("model"),
            "repository": item.get("repository"), "issue": item.get("issue"),
            "session": item.get("session_id"), "phase": item.get("phase"),
            "elapsed": item.get("elapsed_seconds"), "tools": item.get("tool_calls"),
            "tool-errors": item.get("tool_errors"),
            "tests": item.get("test_observations"), "files": ", ".join(str(path) for path in files),
            "durable": durable, "tokens": item.get("token_usage"),
        },
    )
    if edge:
        return (
            f'<button type="button" id="{prefix}-{index}" class="{css} state-{state}" {attributes}>'
            f'<span>{html.escape(label)}</span><small>{html.escape(kind)} · {html.escape(state)}</small></button>'
        )
    return (
        f'<button type="button" id="{prefix}-{index}" class="{css} state-{state}" {attributes}>'
        f'<span class="brain-node-kind">{html.escape(kind)}</span><strong>{html.escape(label)}</strong>'
        f'<span class="brain-node-state">{html.escape(state)}</span><small>{html.escape(age)}</small></button>'
    )


def _render_agent_brain(projection: dict[str, Any]) -> str:
    nodes = list(projection.get("nodes") or ())[:128]
    edges = list(projection.get("edges") or ())[:192]
    projection_time = projection.get("observed_at")
    region_nodes: dict[str, list[str]] = {key: [] for key, _, _ in _BRAIN_REGIONS}
    for index, node in enumerate(nodes):
        if isinstance(node, dict):
            region_nodes[_brain_region(node)].append(_brain_button(node, index, projection_time))
    regions = []
    for key, title, description in _BRAIN_REGIONS:
        content = "".join(region_nodes[key]) or '<p class="brain-region-empty">No current evidence</p>'
        regions.append(
            f'<section class="brain-region region-{key}" aria-labelledby="brain-region-{key}">'
            f'<h3 id="brain-region-{key}">{html.escape(title)}</h3><p>{html.escape(description)}</p>'
            f'<div class="brain-region-nodes">{content}</div></section>'
        )
    signals = "".join(
        _brain_button(edge, index, projection_time, edge=True)
        for index, edge in enumerate(edges) if isinstance(edge, dict)
    ) or '<p class="brain-empty">No directed activity has been observed yet.</p>'
    source_state = str(projection.get("source_state") or "unavailable")
    diagnostics = list(projection.get("diagnostics") or ())
    diagnostic_html = "".join(f'<li>{html.escape(str(item))}</li>' for item in diagnostics)
    empty_note = "" if nodes else (
        '<div class="brain-teaching" role="note"><strong>No live worker evidence yet.</strong> '
        'Start a v5 local worker or configure <code>--v5-runs</code>. Empty regions are unknown, not idle.</div>'
    )


    partial_note = "" if source_state == "ok" else (
        f'<div class="brain-teaching" role="note"><strong>Evidence {html.escape(source_state)}.</strong> '
        'The topology shows only what could be safely normalized.</div>'
    )
    diagnostics_block = f'<ul class="brain-diagnostics">{diagnostic_html}</ul>' if diagnostics else ""
    event_items = []
    for index, event in enumerate(list(projection.get("events") or ())[:160]):
        if not isinstance(event, dict):
            continue
        kind = str(event.get("kind") or "event")
        state = _brain_state(event.get("status"))
        observed = event.get("observed_at") or "time unknown"
        attrs = _microscope_attrs(
            key=f'event:{event.get("source_ref")}:{index}', label=f'{kind} · {event.get("run_id", "unknown")}',
            kind=kind, state=state, observed=observed, reason=event.get("reason"),
            source=event.get("source_ref"), age=_brain_age(event.get("observed_at"), projection_time),
            extra={"run": event.get("run_id"), "phase": event.get("status")},
        )
        event_items.append(
            f'<li><button type="button" class="brain-event state-{state}" {attrs}>'
            f'<time>{html.escape(str(observed))}</time><strong>{html.escape(kind)}</strong>'
            f'<span>{html.escape(str(event.get("reason") or "normalized event"))}</span>'
            f'<small>{html.escape(str(event.get("source_ref") or "source unavailable"))}</small></button></li>'
        )
    event_rail = "".join(event_items) or '<li class="brain-empty">No normalized events observed.</li>'
    handoff_items = "".join(
        f'<li><strong>{html.escape(str(item.get("from", "unknown")))} → {html.escape(str(item.get("to", "unknown")))}</strong>'
        f'<span>{html.escape(str(item.get("reason", "evidence unavailable")))}</span></li>'
        for item in list(projection.get("handoffs") or ())[:64] if isinstance(item, dict)
    ) or '<li class="brain-empty">No evidence-backed handoff observed.</li>'
    signal_items = "".join(
        f'<li><strong>{html.escape(str(item.get("kind", "notice")))}</strong>'
        f'<span>{html.escape(str(item.get("reason", "diagnostic evidence unavailable")))}</span></li>'
        for item in list(projection.get("signals") or ())[:64] if isinstance(item, dict)
    ) or '<li class="brain-empty">No repeated activity, tool error, or long silence observed.</li>'
    criteria_items = "".join(
        f'<li><span class="criterion-state">{html.escape(str(item.get("state", "unknown")))}</span>'
        f'<strong>{html.escape(str(item.get("criterion", "criterion")))}</strong>'
        f'<small>{html.escape(str(item.get("reason", "evidence unavailable")))}</small></li>'
        for item in list(projection.get("criteria_states") or ()) if isinstance(item, dict)
    )
    return (
        '<section class="agent-brain" aria-labelledby="agent-brain-title">'
        '<div class="brain-heading"><div><span class="eyebrow">LIVE EXECUTION MODEL</span>'
        '<h2 id="agent-brain-title">Agent brain</h2><p>Observable coordination, not private reasoning or chain-of-thought.</p></div>'
        f'<span class="brain-source state-{_brain_state(source_state)}">evidence · {html.escape(source_state)}</span></div>'
        f'{empty_note}{partial_note}{diagnostics_block}'
        f'<div class="brain-map">{"".join(regions)}</div>'
        '<div class="brain-lower"><section class="brain-signals" aria-labelledby="brain-signals-title">'
        f'<h3 id="brain-signals-title">Observed signal paths</h3><div class="brain-edge-list">{signals}</div></section>'
        '<aside class="brain-inspector" aria-live="polite" aria-labelledby="brain-detail-title">'
        '<span class="eyebrow">SELECTED EVIDENCE</span><h3 id="brain-detail-title">Choose a node or signal</h3>'
        '<dl><div><dt>Kind</dt><dd id="brain-detail-kind">—</dd></div><div><dt>State</dt><dd id="brain-detail-state">—</dd></div>'
        '<div><dt>Last observed</dt><dd id="brain-detail-age">—</dd></div><div><dt>Reason</dt><dd id="brain-detail-reason">—</dd></div>'
        '<div><dt>Source</dt><dd id="brain-detail-source">—</dd></div><div><dt>Run / phase</dt><dd id="brain-detail-run">—</dd></div>'
        '<div><dt>Role / model</dt><dd id="brain-detail-role">—</dd></div><div><dt>Identity</dt><dd id="brain-detail-identity">—</dd></div>'
        '<div><dt>Objective</dt><dd id="brain-detail-objective">—</dd></div>'
        '<div><dt>Elapsed</dt><dd id="brain-detail-elapsed">—</dd></div><div><dt>Activity</dt><dd id="brain-detail-activity">—</dd></div>'
        '<div><dt>Files</dt><dd id="brain-detail-files">—</dd></div><div><dt>Durable refs</dt><dd id="brain-detail-durable">—</dd></div>'
        '<div><dt>Tokens</dt><dd id="brain-detail-tokens">—</dd></div></dl></aside></div>'
        '<div class="microscope-grid"><section class="event-rail" aria-labelledby="event-rail-title"><h3 id="event-rail-title">Chronological evidence rail</h3>'
        f'<ol>{event_rail}</ol></section><section class="microscope-notices"><h3>Evidence handoffs</h3><ul>{handoff_items}</ul>'
        f'<h3>Neutral diagnostics</h3><ul>{signal_items}</ul><h3>Acceptance evidence</h3><ul class="criteria-list">{criteria_items}</ul>'
        '<p class="brain-caution">File activity alone never verifies semantic completion.</p></section></div></section>'
    )


def _render_strategy(strategy: dict[str, Any], runs: list[dict[str, Any]], session_id: str | None) -> str:
    derivations = strategy.get("derivations", {})
    history_exclusions = strategy.get("facts", {}).get("run_history_exclusions", 0)
    bottlenecks = "".join(
        f'<li><strong>{item["count"]}</strong><span>{html.escape(str(item["kind"]).replace("-", " "))}</span>'
        f'<small>{html.escape(str(item["source"]))}</small></li>'
        for item in derivations.get("bottlenecks", [])
    )
    cohort_rows = []
    metric_names = (
        "delivery", "adjudication", "corrections", "latency_seconds", "tokens",
        "tool_errors", "infrastructure_failure",
    )
    for cohort in derivations.get("cohorts", []):
        cells = []
        for name in metric_names:
            metric = cohort["metrics"][name]
            value = metric["value"]
            if value is None:
                display = "insufficient evidence"
            elif name in {"delivery", "adjudication", "infrastructure_failure"}:
                display = f"{value * 100:.1f}%"
            elif name in {"latency_seconds", "tokens"}:
                display = f"{value:.1f} mean"
            else:
                display = str(value)
            cells.append(
                f'<td><strong>{html.escape(display)}</strong><small>n={metric["numerator"]} / d={metric["denominator"]} '
                f'· excluded {metric["exclusions"]}<br>{html.escape(str(metric["source"]))}</small></td>'
            )
        warning = (
            f'<span class="sample-warning">{html.escape(cohort["sample_warning"])}</span>'
            if cohort.get("sample_warning") else ""
        )
        cohort_rows.append(
            f'<tr><th>{html.escape(cohort["model"])}<small>{html.escape(cohort["route"])} · '
            f'{html.escape(cohort["task_class"])} · {cohort["runs"]} runs</small>{warning}</th>{"".join(cells)}</tr>'
        )
    cohort_html = "".join(cohort_rows) or '<tr><td colspan="8">No comparable run cohorts.</td></tr>'
    comparison = derivations.get("comparison", {})
    selected = comparison.get("run_ids", [])

    def options(position: int) -> str:
        selected_id = selected[position] if len(selected) > position else None
        return "".join(
            f'<option value="{html.escape(str(run["run_id"]), quote=True)}"'
            f'{" selected" if run["run_id"] == selected_id else ""}>{html.escape(str(run["run_id"]))} · '
            f'{html.escape(str(run.get("role") or "unknown"))} · {html.escape(str(run.get("model") or "unknown"))}</option>'
            for run in runs[-128:]
        )

    session_input = (
        f'<input type="hidden" name="session" value="{html.escape(session_id, quote=True)}">'
        if session_id else ""
    )
    comparison_rows = "".join(
        f'<tr class="{"comparison-divergence" if stage["diverges"] else ""}"><th>{html.escape(stage["stage"])}</th>'
        f'<td>{html.escape(str(stage["left"]["value"]))}</td><td>{html.escape(str(stage["right"]["value"]))}</td>'
        f'<td>{"diverges" if stage["diverges"] else ("aligned" if stage["comparable"] else "incomparable")}</td></tr>'
        for stage in comparison.get("stages", [])
    ) or '<tr><td colspan="4">Comparison waiting for selection.</td></tr>'
    comparison_note = (
        f'First observable divergence: {html.escape(str(comparison["first_divergence"]))}'
        if comparison.get("first_divergence")
        else html.escape(str(comparison.get("reason") or "Choose two distinct runs."))
    )
    annotations = strategy.get("facts", {}).get("annotations", [])
    annotation_html = "".join(
        f'<li><strong>{html.escape(str(item["kind"]))}</strong><span>{html.escape(str(item["summary"]))}</span>'
        f'<small>runs {html.escape(", ".join(item["run_ids"]))} · evidence '
        f'{html.escape(", ".join(item["evidence_refs"]) or "none")} · {html.escape(str(item["source"]))}</small></li>'
        for item in annotations
    ) or '<li class="brain-empty">No durable annotations configured.</li>'
    return (
        '<details class="strategy-lab"><summary><span class="eyebrow">STRATEGY LAB</span>'
        '<strong>Evidence-backed organization diagnostics</strong><span>No synthetic score · no automatic tuning</span></summary>'
        '<section class="strategy-section"><h3>Deterministic bottleneck counts</h3><ul class="bottleneck-grid">'
        f'{bottlenecks}</ul></section><section class="strategy-section"><h3>Run comparison</h3>'
        f'<form class="compare-form" method="get">{session_input}<label>Execution A<select name="compare_a" required>{options(0)}</select></label>'
        f'<label>Execution B<select name="compare_b" required>{options(1)}</select></label><button type="submit" class="session-submit">Compare</button></form>'
        f'<p class="comparison-note">{comparison_note}</p><div class="table-wrap"><table><thead><tr><th>Stage</th>'
        f'<th>{html.escape(str(selected[0])) if len(selected) > 0 else "Execution A"}</th><th>{html.escape(str(selected[1])) if len(selected) > 1 else "Execution B"}</th><th>Relationship</th></tr></thead><tbody>{comparison_rows}</tbody></table></div></section>'
        f'<section class="strategy-section"><h3>Cohorts · separate measures</h3><p class="strategy-note">Every cell shows numerator, denominator, exclusions, and source. Associations are not causal. Older run history excluded by bound: {history_exclusions}.</p>'
        f'<div class="table-wrap"><table class="cohort-table"><thead><tr><th>Model / route / task</th><th>Delivery</th><th>Adjudication</th><th>Corrections</th><th>Latency</th><th>Tokens</th><th>Tool errors</th><th>Infrastructure</th></tr></thead><tbody>{cohort_html}</tbody></table></div></section>'
        f'<section class="strategy-section"><h3>Human and Codex annotations</h3><p class="strategy-note">Facts and deterministic derivations above are computed; records below are attributed annotations.</p><ul class="annotation-list">{annotation_html}</ul></section></details>'
    )


def _render_benchmark(snapshot: dict[str, Any]) -> str:
    """Render paired-model benchmark summary with case/model selectors and outcome cards."""
    manifest = snapshot.get("benchmark_manifest")
    attempts = snapshot.get("benchmark_attempts", [])
    summaries = snapshot.get("benchmark_summaries", [])

    if not manifest and not attempts and not summaries:
        return '<section class="benchmark-lab" aria-labelledby="benchmark-title"><h2 id="benchmark-title">Paired-model benchmark</h2><p class="empty">No benchmark data available.</p></section>'

    manifest_id = manifest.get("manifest_id") if manifest else "unknown"
    description = manifest.get("description") if manifest else ""

    # Group summaries by fixture
    fixture_summaries: dict[str, list[dict[str, Any]]] = {}
    for summary in summaries:
        fixture_id = summary.get("fixture_id", "unknown")
        fixture_summaries.setdefault(fixture_id, []).append(summary)

    # Collect unique models and categories
    models = list(set(
        summary.get("left_model") or summary.get("right_model")
        for summary in summaries
        if summary.get("left_model") or summary.get("right_model")
    ))
    models.sort()

    categories = list(set(summary.get("category") for summary in summaries if summary.get("category")))
    categories.sort()

    # Generate fixture selection options
    fixture_ids = sorted(fixture_summaries.keys())
    fixture_options = "".join(
        f'<option value="{html.escape(fid)}">{html.escape(fid)}</option>'
        for fid in fixture_ids
    )

    # Generate model options
    model_options = "".join(
        f'<option value="{html.escape(m)}">{html.escape(m)}</option>'
        for m in models
    )

    # Generate category options
    category_options = "".join(
        f'<option value="{html.escape(c)}">{html.escape(c)}</option>'
        for c in categories
    )

    # Generate paired comparison cards
    cards_html = ""
    for summary in summaries:
        fixture_id = summary.get("fixture_id", "unknown")
        left_model = summary.get("left_model") or "unknown"
        right_model = summary.get("right_model") or "unknown"
        left_tokens = summary.get("left_tokens")
        right_tokens = summary.get("right_tokens")
        left_elapsed = summary.get("left_elapsed")
        right_elapsed = summary.get("right_elapsed")
        left_correct = summary.get("left_correct")
        right_correct = summary.get("right_correct")
        correct_first = summary.get("correct_first")
        tie = summary.get("tie")
        inconclusive = summary.get("inconclusive")
        notes = summary.get("notes", "")

        # Outcome card styling
        if correct_first:
            outcome_class = "outcome-left-wins"
            outcome_label = "Left model correct-first"
        elif tie:
            outcome_class = "outcome-tie"
            outcome_label = "Tie: both correct"
        elif inconclusive:
            outcome_class = "outcome-inconclusive"
            outcome_label = "Inconclusive: both failed"
        else:
            outcome_class = "outcome-mixed"
            outcome_label = "Mixed outcomes"

        # Tokens comparison
        tokens_display = ""
        if left_tokens is not None and right_tokens is not None:
            tokens_display = (
                f'<div class="metric-row"><span class="metric-label">Tokens</span>'
                f'<span class="metric-value left">A: {left_tokens:,}</span>'
                f'<span class="metric-value right">B: {right_tokens:,}</span></div>'
            )
        elif left_tokens is not None:
            tokens_display = f'<div class="metric-row"><span class="metric-label">Tokens A</span><span class="metric-value">{left_tokens:,}</span></div>'
        elif right_tokens is not None:
            tokens_display = f'<div class="metric-row"><span class="metric-label">Tokens B</span><span class="metric-value">{right_tokens:,}</span></div>'

        # Elapsed comparison
        elapsed_display = ""
        if left_elapsed is not None and right_elapsed is not None:
            elapsed_display = (
                f'<div class="metric-row"><span class="metric-label">Elapsed</span>'
                f'<span class="metric-value left">A: {left_elapsed:.1f}s</span>'
                f'<span class="metric-value right">B: {right_elapsed:.1f}s</span></div>'
            )
        elif left_elapsed is not None:
            elapsed_display = f'<div class="metric-row"><span class="metric-label">Elapsed A</span><span class="metric-value">{left_elapsed:.1f}s</span></div>'
        elif right_elapsed is not None:
            elapsed_display = f'<div class="metric-row"><span class="metric-label">Elapsed B</span><span class="metric-value">{right_elapsed:.1f}s</span></div>'

        # Correctness display
        correct_display = ""
        if left_correct is not None and right_correct is not None:
            left_ok = '✓' if left_correct else '✗'
            right_ok = '✓' if right_correct else '✗'
            correct_display = (
                f'<div class="metric-row"><span class="metric-label">Correct</span>'
                f'<span class="metric-value left">{left_ok} A</span>'
                f'<span class="metric-value right">{right_ok} B</span></div>'
            )

        # Raw run links
        left_run_id = summary.get("left_attempt_id", "")
        right_run_id = summary.get("right_attempt_id", "")

        card_html = (
            f'<details class="benchmark-card {outcome_class}" open>'
            f'<summary class="benchmark-summary"><strong>{html.escape(fixture_id)}</strong>'
            f'<span class="benchmark-label">{html.escape(outcome_label)}</span></summary>'
            f'<div class="benchmark-metrics"><div class="metrics-left"><strong>Model A</strong>'
            f'<span class="benchmark-model">{html.escape(left_model)}</span>{tokens_display}{elapsed_display}{correct_display}</div>'
            f'<div class="metrics-right"><strong>Model B</strong>'
            f'<span class="benchmark-model">{html.escape(right_model)}</span></div></div>'
            f'<div class="benchmark-notes"><strong>Analysis:</strong> {html.escape(notes)}</div>'
            f'<div class="benchmark-links"><a href="#run-{html.escape(left_run_id)}" class="raw-run-link">Raw run A</a>'
            f'<a href="#run-{html.escape(right_run_id)}" class="raw-run-link">Raw run B</a></div>'
            f'</details>'
        )
        cards_html += card_html

    if not cards_html:
        cards_html = '<p class="empty">No paired comparisons available.</p>'

    return (
        '<section class="benchmark-lab" aria-labelledby="benchmark-title">'
        f'<h2 id="benchmark-title">Paired-model benchmark</h2>'
        f'<p class="benchmark-desc">{html.escape(description)}</p>'
        f'<div class="benchmark-controls">'
        f'<label>Fixture<select id="fixture-selector">{fixture_options}</select></label>'
        f'<label>Model<select id="model-selector">{model_options}</select></label>'
        f'<label>Category<select id="category-selector">{category_options}</select></label>'
        f'<button type="button" id="refresh-benchmarks" class="session-submit">Refresh</button>'
        f'</div>'
        f'<div class="benchmark-cards">{cards_html}</div>'
        f'</section>'
    )


FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="14" fill="#0b1020"/>
<circle cx="32" cy="32" r="21" fill="none" stroke="#2a3552" stroke-width="4"/>
<path d="M43 20a17 17 0 1 0 0 24" fill="none" stroke="#59d499" stroke-width="7" stroke-linecap="round"/>
<circle cx="48" cy="16" r="5" fill="#f3c969"/>
</svg>"""


def make_handler(source: DashboardSource, broker: ControlBroker | None = None) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "CodexHarnessDashboard/0.1"

        def do_GET(self) -> None:  # noqa: N802
            parsed_url = urlsplit(self.path)
            route = parsed_url.path
            requested_session = parse_qs(parsed_url.query).get("session", [None])[0]
            query = parse_qs(parsed_url.query)
            compare_values = (query.get("compare_a", [""])[0], query.get("compare_b", [""])[0])
            comparison = compare_values if all(compare_values) else None
            try:
                if route == "/favicon.svg":
                    self._send(HTTPStatus.OK, "image/svg+xml", FAVICON_SVG.encode())
                    return
                if route.startswith("/api/control/runs/"):
                    if broker is None:
                        self._send(HTTPStatus.NOT_FOUND, "application/json", b'{"error":"control-disabled"}\n')
                        return
                    if not broker.authorized(self.headers.get("Authorization")):
                        self._send(HTTPStatus.UNAUTHORIZED, "application/json", b'{"error":"unauthorized"}\n')
                        return
                    run_id = route.removeprefix("/api/control/runs/")
                    payload = broker.snapshot(run_id)
                    self._send(HTTPStatus.OK, "application/json", json.dumps(payload).encode())
                    return
                snapshot = source.snapshot(requested_session, comparison)
                if route == "/":
                    self._send(HTTPStatus.OK, "text/html; charset=utf-8", render_dashboard(snapshot).encode())
                elif route == "/api/snapshot":
                    body = json.dumps(snapshot, indent=2, sort_keys=True).encode()
                    self._send(HTTPStatus.OK, "application/json; charset=utf-8", body)
                elif route == "/dashboard.js":
                    self._send(HTTPStatus.OK, "text/javascript; charset=utf-8", DASHBOARD_JS.encode())
                elif route == "/events":
                    self._events()
                elif route == "/healthz":
                    body = json.dumps({"status": "ok", "diagnostics_read_only": True, "control_enabled": broker is not None}).encode()
                    self._send(HTTPStatus.OK, "application/json", body)
                else:
                    self._send(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"Not found\n")
            except ControlError as exc:
                self._send(HTTPStatus(exc.status), "application/json", json.dumps({"error": exc.code}).encode())
            except (OSError, ValueError) as exc:
                body = json.dumps({"error": type(exc).__name__, "read_only": True}).encode()
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, "application/json", body)

        def do_POST(self) -> None:  # noqa: N802
            if broker is None:
                self._method_not_allowed()
                return
            if not broker.authorized(self.headers.get("Authorization")):
                self._send(HTTPStatus.UNAUTHORIZED, "application/json", b'{"error":"unauthorized"}\n')
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 2 or length > 65536:
                    raise ControlError(400, "invalid-body-length")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ControlError(400, "invalid-body")
                pieces = [piece for piece in urlsplit(self.path).path.split("/") if piece]
                if len(pieces) != 6 or pieces[:3] != ["api", "control", "runs"]:
                    raise ControlError(404, "control-route-not-found")
                _, _, _, run_id, kind, request_id = pieces
                actor = self.headers.get("X-Operator", "operator")
                if kind == "permissions":
                    broker.decide_permission(run_id, request_id, payload.get("decision"), actor)
                elif kind == "questions":
                    broker.answer_question(
                        run_id, request_id, payload.get("answers"),
                        payload.get("decision") == "reject", actor,
                    )
                else:
                    raise ControlError(404, "control-route-not-found")
                self._send(HTTPStatus.OK, "application/json", b'{"status":"accepted"}\n')
            except json.JSONDecodeError:
                self._send(HTTPStatus.BAD_REQUEST, "application/json", b'{"error":"invalid-json"}\n')
            except ControlError as exc:
                self._send(HTTPStatus(exc.status), "application/json", json.dumps({"error": exc.code}).encode())

        def do_PUT(self) -> None:  # noqa: N802
            self._method_not_allowed()

        do_PATCH = do_PUT
        do_DELETE = do_PUT

        def _method_not_allowed(self) -> None:
            self._send(HTTPStatus.METHOD_NOT_ALLOWED, "application/json", b'{"error":"read-only"}\n')

        def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _events(self) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            fingerprint = None
            heartbeat = time.monotonic()
            try:
                while True:
                    current = source.fingerprint()
                    if current != fingerprint:
                        self.wfile.write(b"event: snapshot\ndata: {\"changed\":true}\n\n")
                        self.wfile.flush()
                        fingerprint = current
                    elif time.monotonic() - heartbeat >= 15:
                        self.wfile.write(b": heartbeat\n\n")
                        self.wfile.flush()
                        heartbeat = time.monotonic()
                    time.sleep(0.5)
            except (BrokenPipeError, ConnectionResetError):
                return

        def log_message(self, format: str, *args: object) -> None:
            return

    return DashboardHandler


def render_dashboard(snapshot: dict[str, Any]) -> str:
    summary = snapshot["summary"]
    views = snapshot["runs"]

    # Render operational verdict and attention queue
    verdict = snapshot.get("operational_verdict", {
        "severity": "no-data",
        "headline": "No diagnostic data",
        "reasons": [],
        "source_references": [],
        "detail_counts": {},
        "oldest_unadjudicated_at": None,
    })
    attention_queue = snapshot.get("attention_queue", [])
    verdict_html = _render_operational_verdict(
        verdict, attention_queue, snapshot.get("attention_total", len(attention_queue)),
    )

    cards = [
        ("Runs", summary["runs"]),
        ("Adjudicated", summary["adjudicated_runs"]),
        ("Needs judgment", summary["unadjudicated_runs"]),
        ("Contradictions", summary["contradictions"]),
        ("Interrupted", summary["interrupted_runs"]),
        ("Local tokens", f"{summary['local_tokens']:,}"),
        ("Recorded frontier tokens", f"{summary['frontier_tokens']:,}"),
        ("Codex interventions", summary["codex_interventions"]),
    ]
    card_html = "".join(
        f'<section class="card"><span>{html.escape(str(label))}</span><strong>{html.escape(str(value))}</strong></section>'
        for label, value in cards
    )
    rows = "".join(_run_row(view) for view in views) or '<tr><td colspan="10">No diagnostic runs recorded.</td></tr>'
    needs_judgment_count = sum(1 for view in views if _triage_bucket(view) == "needs-judgment")
    contradiction_count = sum(1 for view in views if _triage_bucket(view) == "contradiction")
    triage_toolbar = (
        '<div class="triage-toolbar">'
        f'<button type="button" class="triage-tab" data-filter="needs-judgment">Needs judgment · {needs_judgment_count}</button>'
        f'<button type="button" class="triage-tab" data-filter="contradiction">Contradictions · {contradiction_count}</button>'
        f'<button type="button" class="triage-tab" data-filter="all">All · {len(views)}</button>'
        '<input type="search" class="run-filter" id="run-filter" placeholder="Filter by run, role, model, task…" aria-label="Filter run history">'
        '</div>'
    )
    plans_by_identity = {
        (item["session_id"], item["repository"], item["operation_id"], item["issue"]): item
        for item in snapshot.get("plans", ())
    }
    operation_keys = {
        (item["session_id"], item["repository"], item["operation_id"], item["issue"])
        for item in snapshot["operations"]
    }
    operations = "".join(
        _operation_pair(item, plans_by_identity.get((item["session_id"], item["repository"], item["operation_id"], item["issue"])))
        for item in snapshot["operations"]
    )
    operations += "".join(
        _operation_pair(None, plan) for key, plan in plans_by_identity.items() if key not in operation_keys
    )
    operations = operations or '<p class="empty">No workflow or plan events recorded.</p>'
    codex = snapshot["codex"]
    freshness = snapshot["freshness"]
    age_seconds = freshness["newest_age_seconds"]
    newest_text = _format_age(age_seconds) if age_seconds is not None else "No data"
    freshness_severity = _freshness_severity(age_seconds)
    source_problems = [source for source in snapshot["sources"] if source["state"] != "ok"]
    source_banner = ""
    if source_problems:
        problem_items = "".join(
            f'<li><span class="sev-chip {html.escape(source["state"])}">{html.escape(source["name"])} · {html.escape(source["state"])}</span>'
            f'<span class="source-hint">{html.escape(_SOURCE_HINTS.get(source["state"], "check the source file"))}</span></li>'
            for source in source_problems
        )
        source_banner = f'<section class="source-warning"><strong>Source warning</strong><ul class="source-warning-list">{problem_items}</ul></section>'
    if snapshot["publication_failures"]:
        failures = snapshot["publication_failures"]
        failure_items = "".join(
            f'<li><code>{html.escape(str(item["run_id"]))}</code> · {html.escape(str(item["phase"]))} → {html.escape(str(item["target"]))}'
            f' · {html.escape(str(item["error_class"]))} · {html.escape(str(item["observed_at"]))}</li>'
            for item in failures
        )
        source_banner += (
            '<details class="source-warning publication-failures">'
            f'<summary><strong>Telemetry publication failures:</strong> {len(failures)}</summary>'
            f'<ul class="failure-list">{failure_items}</ul></details>'
        )
    telemetry_cards = []
    if codex["tokens_used"] is not None:
        telemetry_cards.append(("Interactive Codex tokens", f"{codex['tokens_used']:,}", codex["source"]))
    if codex["context_used"] is not None and codex["context_window"] is not None:
        telemetry_cards.append(("Codex context", f"{codex['context_used']:,} / {codex['context_window']:,}", codex["source"]))
    if codex["plan_remaining_percent"] is not None:
        telemetry_cards.append(("Plan remaining", f"{codex['plan_remaining_percent']:.1f}%", codex["source"]))
    if codex["plan_refresh_at"] is not None:
        telemetry_cards.append(("Plan refresh", codex["plan_refresh_at"], "Never estimated"))
    telemetry_cards.append(("Source update age", newest_text, f"{freshness['existing_files']} / {freshness['source_files']} sources present"))
    telemetry_html = "".join(
        f'<section class="card"><span>{html.escape(str(label))}</span><strong>{html.escape(str(value))}</strong><small>{html.escape(str(note))}</small></section>'
        for label, value, note in telemetry_cards
    )
    session_options = ['<option value="">All sessions</option>'] + [
        f'<option value="{html.escape(item["session_id"])}"{" selected" if snapshot["selected_session"] == item["session_id"] else ""}>'
        f'{html.escape(item["label"])} · {html.escape(item["status"])} · {item["operations"]} ops'
        f'{"" if item["session_id"] == "legacy" else " · " + html.escape(item["session_id"][:8])}</option>'
        for item in snapshot["sessions"]
    ]
    session_picker = (
        '<form class="session-picker" method="get"><label>Session '
        f'<select name="session" id="session-select">{"".join(session_options)}</select></label> '
        '<button type="submit" class="session-submit">Go</button></form>'
    )
    accounting_html = _accounting_html(snapshot["accounting"], views)
    brain_html = _render_agent_brain(snapshot.get("live_execution", {}))
    strategy_html = _render_strategy(
        snapshot.get("strategy", {}), views, snapshot.get("selected_session"),
    )
    benchmark_html = _render_benchmark(snapshot)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<title>Codex agent harness diagnostics</title>
<style>
:root{{--bg:#0b1020;--panel:#151c30;--ink:#eef2ff;--muted:#9aa6c3;--line:#2a3552;--good:#59d499;--warn:#f3c969;--bad:#ff7885}}
*{{box-sizing:border-box}} html,body{{max-width:100%;overflow-x:hidden}} body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 ui-sans-serif,system-ui,sans-serif}}
main{{width:100%;max-width:1500px;margin:auto;padding:28px}} h1{{margin:0;font-size:25px}} .subtitle{{color:var(--muted);margin:5px 0 22px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px;margin-bottom:22px}} .card{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}}
.card span{{display:block;color:var(--muted);font-size:12px}} .card strong{{display:block;font-size:21px;margin-top:3px}}
.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:10px}} table{{width:100%;border-collapse:collapse;background:var(--panel)}}
.operation{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;margin:0 0 14px;overflow:hidden}} .operation-head{{display:flex;gap:16px;justify-content:space-between;flex-wrap:wrap}} .flow{{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));gap:8px;margin-top:12px;padding:2px;min-width:0}} .milestone{{position:relative;border:1px solid var(--line);border-radius:8px;padding:9px;min-width:0;overflow-wrap:anywhere}} .milestone.pending{{color:#626b7c;border-color:#343946;background:#181b22}} .milestone.not-applicable{{color:#737b8c;border-color:#343946;background:transparent}} .milestone.active{{border-color:var(--warn)}} .milestone.active::after{{content:"";position:absolute;top:4px;left:4px;width:6px;height:6px;border-radius:50%;background:#fff5bd;box-shadow:0 0 3px 1px rgba(243,201,105,.5);pointer-events:none}} @media (prefers-reduced-motion:no-preference){{.milestone.active::after{{animation:milestone-orbit 4s linear infinite}}@keyframes milestone-orbit{{0%,100%{{top:4px;left:4px}}25%{{top:4px;left:calc(100% - 10px)}}50%{{top:calc(100% - 10px);left:calc(100% - 10px)}}75%{{top:calc(100% - 10px);left:4px}}}}}}@media (prefers-reduced-motion:reduce){{.milestone.active::after{{top:4px;right:4px;left:auto}}}} .milestone.passed{{border-color:var(--good)}} .milestone.failed,.milestone.interrupted,.milestone.blocked,.milestone.conflicting{{border-color:var(--bad)}} .milestone-name{{display:block;font-size:11px;color:var(--muted)}} .milestone-activities{{margin-top:6px}} .milestone-activity{{display:inline-block;background:var(--line);color:var(--ink);border-radius:4px;padding:2px 6px;font-size:9px;margin-right:4px;margin-bottom:4px}} .unassigned-activities{{margin:9px 2px 0;padding:7px 10px;border:1px dashed var(--warn);border-radius:5px;color:var(--warn)}} .correction-loop{{margin:9px 2px 0;padding:7px 10px;border-left:3px solid var(--warn);background:#272417;color:var(--warn);border-radius:4px}} .workflow-diagnostic{{display:inline-block;margin-top:8px;padding:3px 7px;border:1px solid var(--bad);color:var(--bad);border-radius:5px;font-size:11px}} @media (max-width:1000px){{.flow{{grid-template-columns:repeat(4,minmax(0,1fr))}}}} @media (max-width:560px){{main{{padding:16px}}.flow{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
.telemetry{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px;margin:0 0 22px}} .empty{{color:var(--muted)}} #connection{{color:var(--warn)}}
.operation-pair{{display:grid;grid-template-columns:minmax(0,1fr);gap:10px;margin-bottom:14px;min-width:0}} .operation-pair .operation{{margin:0}} .running-plan{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;min-width:0}} .plan-head{{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}} .plan-objective{{margin:9px 0;color:var(--ink)}} .plan-steps{{list-style:none;padding:0;margin:10px 0 0;display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:6px}} .plan-step{{position:relative;border:1px solid var(--line);border-radius:7px;padding:8px;min-width:0;overflow-wrap:anywhere}} .plan-step.pending{{color:#626b7c;border-color:#343946;background:#181b22}} .plan-step.active{{border-color:var(--warn)}} .plan-step.completed,.plan-step.skipped{{color:var(--muted)}} .plan-step.blocked{{border-color:var(--bad)}} .plan-step.active::after{{content:"";position:absolute;top:4px;left:4px;width:6px;height:6px;border-radius:50%;background:#fff5bd;box-shadow:0 0 3px rgba(243,201,105,.5)}} @media (prefers-reduced-motion:no-preference){{.plan-step.active::after{{animation:plan-focus 4s linear infinite}}@keyframes plan-focus{{0%,100%{{top:4px;left:4px}}25%{{top:4px;left:calc(100% - 10px)}}50%{{top:calc(100% - 10px);left:calc(100% - 10px)}}75%{{top:calc(100% - 10px);left:4px}}}}}} @media (prefers-reduced-motion:reduce){{.plan-step.active::after{{top:4px;right:4px;left:auto}}}} .plan-state{{display:block;font-size:10px;text-transform:uppercase;color:var(--muted)}} .plan-diagnostic{{display:inline-block;margin:7px 5px 0 0;color:var(--bad);font-size:11px}} .plan-change{{border-left:3px solid var(--warn);padding-left:8px;color:var(--warn)}} .plan-history{{margin-top:10px;color:var(--muted)}}
.session-picker{{margin:0 0 14px;display:flex;gap:8px;align-items:center;flex-wrap:wrap}} select{{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:7px;font:inherit;max-width:100%}}
.session-submit{{background:var(--panel);color:var(--muted);border:1px solid var(--line);border-radius:7px;padding:7px 12px;font:inherit;cursor:pointer}} .session-submit:hover{{color:var(--ink)}}
.source-warning{{border:1px solid var(--bad);background:#3a1e2a;border-radius:8px;padding:10px;margin-bottom:14px}}
th,td{{padding:10px 11px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}} th{{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em}}
tr.contradiction{{background:#3a1e2a}} .passed{{color:var(--good)}} .failed,.interrupted,.timed-out,.cancelled{{color:var(--bad)}} .unadjudicated,.unknown{{color:var(--warn)}}
code{{color:#cbd5ff}} details{{max-width:380px;white-space:normal}} footer{{color:var(--muted);margin-top:16px}}
.freshness{{padding:2px 9px;border-radius:999px;font-size:11px;font-weight:600}} .freshness.good{{color:var(--good);background:#173327}} .freshness.warn{{color:var(--warn);background:#332a13}} .freshness.critical{{color:var(--bad);background:#3a1e2a}} .freshness.unknown{{color:var(--muted);background:#1b2338}}
.source-warning-list{{list-style:none;margin:8px 0 0;padding:0;display:grid;gap:6px}} .source-warning-list li{{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}}
.sev-chip{{display:inline-block;padding:2px 8px;border-radius:5px;font-size:11px;font-weight:600;background:#3a1e2a;color:var(--bad)}} .sev-chip.missing{{background:#332a13;color:var(--warn)}}
.source-hint{{color:var(--muted);font-size:12px}}
.publication-failures summary{{cursor:pointer}} .publication-failures[open]{{padding-bottom:10px}} .failure-list{{list-style:none;margin:8px 0 0;padding:0;display:grid;gap:4px;font-size:12px;color:var(--muted);max-width:none}}
.triage-toolbar{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:0 0 10px}}
.triage-tab{{background:var(--panel);color:var(--muted);border:1px solid var(--line);border-radius:999px;padding:5px 12px;font-size:12px;cursor:pointer;font:inherit}}
.triage-tab.active{{color:var(--ink);border-color:var(--good);background:#173327}}
.run-filter{{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:6px 10px;font-size:12px;min-width:220px;font:inherit}}
th.sortable{{cursor:pointer;user-select:none}} th.sortable:hover{{color:var(--ink)}} th.sortable::after{{content:"↕";opacity:.4;margin-left:4px}} th.sortable.sort-asc::after{{content:"↑";opacity:1}} th.sortable.sort-desc::after{{content:"↓";opacity:1}}
.table-empty-note{{color:var(--muted);padding:14px 4px}}
.flow-toggle{{border:none;padding:0;margin-top:12px}} .flow-summary{{list-style:none;cursor:pointer;display:flex;align-items:center;gap:9px;flex-wrap:wrap;padding:2px 0}} .flow-summary::-webkit-details-marker{{display:none}} .flow-summary::before{{content:"▸";color:var(--muted);font-size:10px;width:8px}} details[open]>.flow-summary::before{{content:"▾"}} details[open]>.flow-summary+.flow{{margin-top:10px}}
.rail-dots{{display:inline-flex;gap:4px}} .rail-dot{{width:9px;height:9px;border-radius:50%;background:#343946;display:inline-block}}
.rail-dot.not-applicable{{background:transparent;border:1px solid #343946}} .rail-dot.active{{background:var(--warn)}} .rail-dot.passed{{background:var(--good)}} .rail-dot.failed,.rail-dot.interrupted,.rail-dot.blocked,.rail-dot.conflicting{{background:var(--bad)}}
.rail-current{{color:var(--muted);font-size:12px}}
.accounting-footnote{{color:var(--muted);font-size:12px;margin:8px 2px 0}}
.card-spark{{margin-top:8px}} .sparkline{{display:block}}
.agent-brain{{background:linear-gradient(145deg,#101a31,#12182a 55%,#17172a);border:1px solid #41527b;border-radius:16px;padding:20px;margin:0 0 18px;box-shadow:0 18px 55px rgba(0,0,0,.22)}}
.brain-heading{{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap;margin-bottom:15px}} .brain-heading h2{{font-size:25px;margin:2px 0}} .brain-heading p{{color:var(--muted);margin:0}} .eyebrow{{color:#8faeff;font-size:10px;font-weight:800;letter-spacing:.14em}}
.brain-source,.brain-node-state{{border:1px solid var(--line);border-radius:999px;padding:3px 8px;font-size:10px;font-weight:700;text-transform:uppercase}} .brain-source{{white-space:nowrap}}
.brain-map{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));grid-template-areas:"intake executive executive routing" "memory action verification tools";gap:10px}} .region-intake{{grid-area:intake}} .region-executive{{grid-area:executive}} .region-routing{{grid-area:routing}} .region-action{{grid-area:action}} .region-verification{{grid-area:verification}} .region-tools{{grid-area:tools}} .region-memory{{grid-area:memory}}
.brain-region{{min-width:0;min-height:135px;border:1px solid var(--line);border-radius:12px;padding:11px;background:rgba(11,16,32,.58)}} .brain-region h3,.brain-signals h3,.brain-inspector h3{{margin:0;font-size:13px}} .brain-region>p{{color:var(--muted);font-size:10px;margin:2px 0 9px}} .brain-region-nodes{{display:grid;gap:6px}} .brain-region-empty,.brain-empty{{color:#68738e;font-size:11px;margin:12px 0}}
.brain-node,.brain-edge{{font:inherit;color:var(--ink);text-align:left;cursor:pointer;background:#18223a;border:1px solid var(--line);border-radius:8px;padding:8px;min-width:0}} .brain-node:hover,.brain-node:focus-visible,.brain-edge:hover,.brain-edge:focus-visible,.brain-select-active{{outline:2px solid #8faeff;outline-offset:1px}} .brain-node strong,.brain-node small,.brain-edge span,.brain-edge small{{display:block;overflow-wrap:anywhere}} .brain-node-kind{{display:block;color:var(--muted);font-size:9px;text-transform:uppercase}} .brain-node-state{{display:inline-block;margin:5px 5px 0 0}} .brain-node small,.brain-edge small{{color:var(--muted);font-size:9px;margin-top:3px}}
.state-active,.state-running,.state-running-tests{{border-color:var(--good)}} .state-waiting-inference,.state-waiting-tool,.state-pending{{border-color:var(--warn)}} .state-failed,.state-interrupted,.state-error{{border-color:var(--bad)}} .state-stale{{border-style:dashed;color:var(--warn)}} .state-unknown,.state-unavailable{{border-style:dotted;color:var(--muted)}} .state-completed{{border-color:#526483;color:#aebad5}}
.brain-lower{{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(260px,1fr);gap:10px;margin-top:10px}} .brain-signals,.brain-inspector{{border:1px solid var(--line);border-radius:12px;padding:12px;background:rgba(11,16,32,.58);min-width:0}} .brain-edge-list{{display:flex;gap:6px;overflow:auto;padding:8px 2px 2px}} .brain-edge{{position:relative;flex:0 0 min(245px,75vw)}} .brain-edge.state-active,.brain-edge.state-running,.brain-edge.state-running-tests,.brain-edge.state-waiting-inference,.brain-edge.state-waiting-tool{{background:linear-gradient(90deg,#18223a,#243654,#18223a);background-size:200% 100%}}
.brain-inspector dl{{margin:8px 0 0;display:grid;gap:7px}} .brain-inspector dl div{{display:grid;grid-template-columns:90px 1fr;gap:8px}} .brain-inspector dt{{color:var(--muted);font-size:10px;text-transform:uppercase}} .brain-inspector dd{{margin:0;overflow-wrap:anywhere}} .brain-teaching{{border-left:3px solid var(--warn);background:#272417;padding:9px 11px;margin-bottom:10px;color:var(--muted)}} .brain-diagnostics{{color:var(--warn);font-size:11px}}
.microscope-grid{{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(280px,1fr);gap:10px;margin-top:10px}} .event-rail,.microscope-notices{{border:1px solid var(--line);border-radius:12px;padding:12px;background:rgba(11,16,32,.58);min-width:0}} .event-rail h3,.microscope-notices h3{{margin:0 0 8px;font-size:13px}} .event-rail ol{{list-style:none;margin:0;padding:0 0 0 12px;border-left:2px solid var(--line);display:grid;gap:5px;max-height:360px;overflow:auto}} .event-rail li{{position:relative}} .event-rail li::before{{content:"";position:absolute;left:-17px;top:13px;width:8px;height:8px;border-radius:50%;background:#7085b3}}
.brain-event{{display:grid;grid-template-columns:minmax(135px,.7fr) minmax(90px,.35fr) minmax(180px,1fr);gap:8px;width:100%;background:#141d31;border:1px solid var(--line);border-radius:7px;padding:7px;color:var(--ink);font:inherit;text-align:left;cursor:pointer}} .brain-event time,.brain-event small{{color:var(--muted);font-size:10px;overflow-wrap:anywhere}} .brain-event span{{overflow-wrap:anywhere}} .brain-event:hover,.brain-event:focus-visible{{outline:2px solid #8faeff;outline-offset:1px}}
.microscope-notices ul{{list-style:none;margin:0 0 12px;padding:0;display:grid;gap:5px}} .microscope-notices li{{display:grid;border-left:2px solid var(--line);padding-left:8px}} .microscope-notices li span,.microscope-notices li small{{color:var(--muted);font-size:10px}} .criterion-state{{color:var(--warn);font-size:9px;text-transform:uppercase}} .brain-caution{{color:var(--warn);font-size:11px;border-top:1px solid var(--line);padding-top:8px}}
.diagnostic-secondary{{margin:0 0 16px}} .diagnostic-secondary>summary{{cursor:pointer;color:var(--muted);margin:0 0 8px}} .diagnostic-secondary .operational-verdict{{margin:0}}
@media (prefers-reduced-motion:no-preference){{.brain-edge.state-active,.brain-edge.state-running,.brain-edge.state-running-tests,.brain-edge.state-waiting-inference,.brain-edge.state-waiting-tool{{animation:brain-signal 2.4s linear infinite}}@keyframes brain-signal{{to{{background-position:-200% 0}}}}}}
@media (prefers-reduced-motion:reduce){{.brain-edge{{animation:none!important}}}}
@media (max-width:900px){{.brain-map{{grid-template-columns:repeat(2,minmax(0,1fr));grid-template-areas:"executive routing" "intake action" "verification tools" "memory memory"}} .brain-lower,.microscope-grid{{grid-template-columns:1fr}}}}
@media (max-width:560px){{.agent-brain{{padding:12px;border-radius:12px}} .brain-map{{grid-template-columns:1fr;grid-template-areas:"executive" "routing" "action" "verification" "tools" "intake" "memory"}} .brain-region{{min-height:0}} .brain-edge{{flex-basis:82vw}} .brain-inspector dl div{{grid-template-columns:72px 1fr}} .brain-event{{grid-template-columns:1fr;gap:2px}}}}
</style>
<style>
.operational-verdict{{border:2px solid var(--line);background:var(--panel);border-radius:12px;padding:16px;margin:0 0 16px}} .operational-verdict.severity-critical{{border-color:var(--bad)}} .operational-verdict.severity-degraded{{border-color:var(--warn)}} .operational-verdict.severity-no-data{{border-color:var(--muted);background:#1b2338}} .operational-verdict.severity-healthy{{border-color:var(--good)}}
.verdict-header{{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:12px}} .verdict-severity{{display:inline-block;padding:4px 10px;border-radius:999px;font-size:11px;font-weight:700;text-transform:uppercase}}
.verdict-severity.critical{{background:var(--bad);color:#000}} .verdict-severity.degraded{{background:var(--warn);color:#000}} .verdict-severity.no-data{{background:var(--muted);color:#fff}} .verdict-severity.healthy{{background:var(--good);color:#000}}
.verdict-details{{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:12px}} .detail-count{{display:inline-block;padding:2px 8px;border-radius:4px;background:#2a3552;color:var(--ink);font-size:12px}}
.verdict-reasons{{list-style:none;margin:0 0 12px;padding:0;display:grid;gap:4px}} .verdict-reasons li{{color:var(--ink);font-size:13px}}
.attention-queue{{max-width:600px}} .attention-summary{{cursor:pointer;display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:2px 0;margin:8px 0 0}} .attention-summary::-webkit-details-marker{{display:none}}
.attention-summary::before{{content:"▸";color:var(--muted);font-size:10px;width:8px}} details[open]>.attention-summary::before{{content:"▾"}}
.attention-list{{list-style:none;margin:8px 0 0;padding:0;display:grid;gap:6px}}
.attention-item{{border:1px solid var(--line);border-radius:6px;padding:8px;display:grid;grid-template-columns:60px minmax(120px,1fr) minmax(160px,2fr) auto;gap:8px;align-items:center}}
.attention-item.critical{{border-color:var(--bad)}} .attention-item.warn{{border-color:var(--warn)}}
.attention-severity{{display:flex;justify-content:center;align-items:center;padding:2px 6px;border-radius:4px;font-size:10px;font-weight:700;text-transform:uppercase;background:#2a3552;color:var(--ink)}}
.attention-severity.CRITICAL{{background:var(--bad);color:#000}} .attention-severity.DEGRADED{{background:var(--warn);color:#000}}
.attention-subject{{font-weight:600;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.attention-reason{{color:var(--muted);font-size:12px;white-space:nowrap}}
.omitted-count{{color:var(--muted);font-size:11px}}
.microscope-trigger{{background:#18223a;color:#b9c9ef;border:1px solid #41527b;border-radius:6px;padding:4px 8px;font:inherit;font-size:10px;cursor:pointer;white-space:nowrap}} .microscope-trigger:hover,.microscope-trigger:focus-visible{{outline:2px solid #8faeff;outline-offset:1px;color:#fff}}
@media (max-width:560px){{.attention-item{{grid-template-columns:55px 1fr}} .attention-reason{{grid-column:1/-1;white-space:normal}}}}
.strategy-lab{{max-width:none;background:var(--panel);border:1px solid #41527b;border-radius:12px;margin:0 0 16px;padding:0}} .strategy-lab>summary{{cursor:pointer;display:flex;gap:12px;align-items:center;flex-wrap:wrap;padding:13px 15px}} .strategy-lab>summary strong{{font-size:15px}} .strategy-lab>summary>span:last-child{{color:var(--muted);font-size:11px;margin-left:auto}} .strategy-section{{border-top:1px solid var(--line);padding:14px}} .strategy-section h3{{margin:0 0 9px}} .strategy-note,.comparison-note{{color:var(--muted);font-size:11px}}
.bottleneck-grid{{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:7px}} .bottleneck-grid li{{border:1px solid var(--line);border-radius:8px;padding:9px}} .bottleneck-grid strong,.bottleneck-grid span,.bottleneck-grid small{{display:block}} .bottleneck-grid strong{{font-size:20px}} .bottleneck-grid span{{text-transform:capitalize}} .bottleneck-grid small,.cohort-table small{{color:var(--muted);font-size:9px;white-space:normal}}
.compare-form{{display:flex;gap:8px;align-items:end;flex-wrap:wrap;margin-bottom:8px}} .compare-form label{{display:grid;gap:3px;color:var(--muted);font-size:10px}} .comparison-divergence{{background:#332a13}} .cohort-table th,.cohort-table td{{vertical-align:top}} .cohort-table td strong,.cohort-table td small,.cohort-table th small{{display:block;white-space:normal}} .sample-warning{{display:inline-block;color:var(--warn);font-size:9px;margin-top:4px}} .annotation-list{{list-style:none;margin:0;padding:0;display:grid;gap:6px}} .annotation-list li{{border-left:2px solid #8faeff;padding-left:9px;display:grid}} .annotation-list small{{color:var(--muted)}}
.console-wrapper{{margin-top:22px;border:1px solid var(--line);border-radius:12px;background:var(--panel);overflow:hidden}}
.console-placeholder{{padding:20px;text-align:center}} .console-placeholder .console-open-btn{{background:linear-gradient(135deg,var(--good),#3bb57d);color:#000;border:none;border-radius:8px;padding:12px 24px;font-size:15px;font-weight:600;cursor:pointer;box-shadow:0 4px 12px rgba(89,212,153,.3);transition:transform .15s,box-shadow .15s}} .console-placeholder .console-open-btn:hover{{transform:translateY(-1px);box-shadow:0 6px 16px rgba(89,212,153,.4)}} .console-placeholder .console-open-btn:active{{transform:translateY(0)}}
.console-panel{{padding:0}} .console-panel[hidden]{{display:none}}
.console-header{{display:flex;justify-content:space-between;align-items:center;padding:14px 16px;border-bottom:1px solid var(--line);background:rgba(21,28,48,.6)}} .console-header h3{{margin:0;font-size:15px}}
.console-close-btn{{background:none;border:none;color:var(--muted);font-size:20px;cursor:pointer;padding:4px 8px;line-height:1;border-radius:4px}} .console-close-btn:hover{{color:var(--ink);background:rgba(42,53,82,.5)}}
.console-connection-form{{padding:20px;max-width:480px;margin:0 auto}} .console-warning{{background:#3a1e2a;border-left:3px solid var(--bad);color:var(--warn);padding:10px 12px;margin:0 0 16px;border-radius:4px;font-size:12px}}
.form-group{{margin-bottom:14px}} .form-group label{{display:block;color:var(--muted);font-size:11px;font-weight:600;margin-bottom:6px}} .form-group input{{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:7px;background:var(--bg);color:var(--ink);font:inherit;box-sizing:border-box;transition:border-color .15s}} .form-group input:focus{{outline:none;border-color:var(--good)}} .form-group small{{display:block;color:var(--muted);font-size:11px;margin-top:5px}}
.console-submit-btn{{width:100%;background:var(--good);color:#000;border:none;border-radius:7px;padding:12px;font-weight:600;cursor:pointer;font:inherit}} .console-submit-btn:hover{{background:#4bc48d}} .console-submit-btn:disabled{{opacity:.6;cursor:not-allowed}}
.console-connected{{padding:16px}} .console-status{{display:flex;align-items:center;gap:10px;margin-bottom:14px;padding-bottom:12px;border-bottom:1px solid var(--line)}} .status-indicator{{width:10px;height:10px;border-radius:50%;background:#343946;display:inline-block}} .status-indicator.connected{{background:var(--good);box-shadow:0 0 0 3px rgba(89,212,153,.3)}} .status-indicator.error{{background:var(--bad)}} .status-indicator.loading{{background:var(--warn);animation:status-pulse 1.5s ease-in-out infinite}} @keyframes status-pulse{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}
.console-disconnect-btn{{background:var(--panel);color:var(--muted);border:1px solid var(--line);border-radius:6px;padding:6px 12px;font:inherit;cursor:pointer}} .console-disconnect-btn:hover{{color:var(--ink);border-color:var(--line)}}
.console-controls{{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin-bottom:14px;padding-bottom:12px;border-bottom:1px solid var(--line)}}
.console-run-info,.console-session-info{{display:flex;align-items:center;gap:6px;color:var(--muted);font-size:11px;background:rgba(42,53,82,.3);padding:4px 8px;border-radius:4px}} .console-run-info strong,.console-session-info strong{{color:var(--ink)}}
.console-filters{{display:flex;gap:8px}} .console-filters label{{display:flex;align-items:center;gap:4px;color:var(--muted);font-size:11px;background:rgba(42,53,82,.3);padding:3px 8px;border-radius:4px;cursor:pointer}} .console-filters label:hover{{background:rgba(42,53,82,.5)}} .console-filters input{{width:12px;height:12px;margin:0;cursor:pointer}}
.console-transcript{{max-height:320px;overflow-y:auto;margin-bottom:12px;padding:10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);font-family:monospace;font-size:12px}} .console-transcript .transcript-empty{{color:var(--muted);font-style:italic}} .console-transcript .event{{padding:6px 8px;margin:4px 0;border-left:3px solid var(--line);background:rgba(255,255,255,.02);border-radius:0 6px 6px 0}} .console-transcript .event.dialogue{{border-color:#8faeff}} .console-transcript .event.tool{{border-color:#59d499}} .console-transcript .event.error{{border-color:var(--bad)}} .console-transcript .event-time{{color:var(--muted);font-size:10px;display:block;margin-bottom:2px}} .console-transcript .event-content{{color:var(--ink);white-space:pre-wrap;word-break:break-word}}
.transcript-footer{{display:flex;justify-content:space-between;align-items:center;margin-bottom:16px}}
.transcript-toggle-btn,.transcript-clear-btn{{background:var(--panel);color:var(--muted);border:1px solid var(--line);border-radius:6px;padding:6px 10px;font-size:11px;cursor:pointer}} .transcript-toggle-btn:hover,.transcript-clear-btn:hover{{color:var(--ink);border-color:var(--line)}} .transcript-toggle-btn[aria-pressed="true"]{{color:var(--ink);background:rgba(89,212,153,.1);border-color:rgba(89,212,153,.3)}}
.console-pending-permissions,.console-pending-questions{{margin-bottom:16px}} .console-pending-permissions h4,.console-pending-questions h4{{margin:0 0 8px;font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em}}
.console-policy{{border-top:1px solid var(--line);padding-top:12px}} .console-policy p{{color:var(--muted);font-size:11px}} .console-policy pre{{max-height:240px;overflow:auto;white-space:pre-wrap;word-break:break-word;background:var(--bg);border:1px solid var(--line);border-radius:7px;padding:10px;font-size:11px}}
.benchmark-lab{{max-width:none;background:var(--panel);border:1px solid #41527b;border-radius:12px;margin:0 0 16px;padding:0}} .benchmark-lab>h2{{padding:14px 16px;margin:0}} .benchmark-desc{{padding:0 16px 14px;color:var(--muted);font-size:13px}} .benchmark-controls{{display:flex;gap:8px;align-items:end;flex-wrap:wrap;margin:0 16px 14px}} .benchmark-controls label{{display:grid;gap:3px;color:var(--muted);font-size:10px}} .benchmark-controls select{{background:var(--bg);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:6px 10px;font:inherit;max-width:140px}} .benchmark-controls .session-submit{{background:var(--good);color:#000}} .benchmark-cards{{padding:0 16px 16px;display:grid;gap:12px}} .benchmark-card{{border:1px solid var(--line);border-radius:8px;padding:12px;background:rgba(255,255,255,.02)}} .benchmark-card.outcome-left-wins{{border-left:3px solid var(--good)}} .benchmark-card.outcome-tie{{border-left:3px solid var(--warn)}} .benchmark-card.outcome-inconclusive{{border-left:3px solid var(--muted)}} .benchmark-card.outcome-mixed{{border-left:3px solid var(--bad)}} .benchmark-summary{{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:8px 10px;margin:0 0 10px}} .benchmark-summary strong{{font-size:14px}} .benchmark-label{{font-size:11px;background:rgba(255,255,255,.1);padding:2px 6px;border-radius:4px}} .benchmark-metrics{{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:8px 10px;background:rgba(255,255,255,.02);border-radius:6px;margin-bottom:8px}} .metrics-left,.metrics-right{{padding:8px;border-radius:6px;background:rgba(255,255,255,.03)}} .metrics-left{{border-right:1px solid var(--line)}} .benchmark-model{{display:block;font-size:13px;color:var(--ink);margin-top:4px}} .metric-row{{display:flex;justify-content:space-between;gap:8px;margin:4px 0;font-size:12px}} .metric-label{{color:var(--muted)}} .metric-value{{font-weight:600}} .metric-value.left{{color:var(--good)}} .metric-value.right{{color:#8faeff}} .benchmark-notes{{padding:8px 10px;color:var(--muted);font-size:12px;border-top:1px solid var(--line);margin-top:8px}} .benchmark-links{{padding:8px 10px;display:flex;gap:8px;flex-wrap:wrap}} .raw-run-link{{display:inline-block;padding:4px 8px;border:1px solid var(--line);border-radius:4px;font-size:11px;color:var(--muted);text-decoration:none}} .raw-run-link:hover{{color:var(--ink);border-color:var(--ink)}} .benchmark-cards .empty{{color:var(--muted);text-align:center;padding:20px}}
.permissions-list,.questions-list{{display:grid;gap:10px}} .empty-message{{color:var(--muted);font-size:12px;text-align:center;padding:12px}} .permission-card,.question-card{{border:1px solid var(--line);border-radius:8px;padding:12px;background:rgba(255,255,255,.03)}}
.permission-card{{border-left:3px solid var(--warn)}} .question-card{{border-left:3px solid #8faeff}}
.permission-id,.question-id{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}}
.permission-pattern,.question-text{{color:var(--ink);font-size:13px;margin:4px 0}}
.question-header,.permission-header{{color:var(--warn);font-size:12px;margin-bottom:6px}}
.permission-options,.question-options{{display:grid;gap:6px;margin:8px 0}}
.permission-option{{padding:8px 10px;border:1px solid var(--line);border-radius:6px;background:var(--bg);cursor:pointer;transition:border-color .15s}} .permission-option:hover{{border-color:var(--warn)}}
.question-option{{padding:8px 10px;border:1px solid var(--line);border-radius:6px;background:var(--bg);cursor:pointer;transition:border-color .15s}} .question-option:hover{{border-color:#8faeff}} .question-option.selected{{border-color:#8faeff;background:rgba(143,174,255,.1)}}
.permission-actions{{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;padding-top:8px;border-top:1px solid var(--line)}}
.question-actions{{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:10px;padding-top:8px;border-top:1px solid var(--line)}}
.console-btn{{padding:6px 12px;border-radius:6px;font-size:11px;cursor:pointer;font:inherit}} .console-btn.once{{background:#1a2b4a;border:1px solid #4a6b9a;color:#8faeff}} .console-btn.once:hover{{background:#233b6a}} .console-btn.always{{background:#2a2417;border:1px solid var(--warn);color:var(--warn)}} .console-btn.always:hover{{background:#3a3427}} .console-btn.reject{{background:#3a1e2a;border:1px solid var(--bad);color:var(--bad)}} .console-btn.reject:hover{{background:#4a2e3a}} .console-btn:disabled{{opacity:.5;cursor:not-allowed}}
.console-btn.submit-question{{background:var(--good);color:#000}} .console-btn.submit-question:hover{{background:#4bc48d}} .console-btn.reject-question{{background:#3a1e2a;border:1px solid var(--bad);color:var(--bad)}}
.console-btn.question-select{{background:var(--panel);color:var(--ink);border:1px solid var(--line)}} .console-btn.question-select:hover{{background:rgba(42,53,82,.5)}} .console-btn.question-select.selected{{border-color:#8faeff;box-shadow:0 0 0 1px var(--ink)}}
@media (max-width:560px){{.console-wrapper{{border-radius:0;border-left:none;border-right:none}} .console-panel{{padding:0}} .console-header{{padding:12px}} .console-header h3{{font-size:14px}} .console-connection-form{{padding:16px}} .console-status{{flex-direction:column;align-items:flex-start}} .console-controls{{flex-direction:column;align-items:stretch}} .console-filters{{flex-wrap:wrap}} .console-filters label{{flex:1}} .console-transcript{{max-height:240px}} .permissions-list,.questions-list{{grid-template-columns:1fr}} .permission-actions{{flex-direction:column}} .question-actions{{flex-direction:column}}}}
</style>
</head><body><main>
<h1>Harness diagnostic loop</h1><p class="subtitle">Release {html.escape(str(snapshot['release']))} · <strong>{html.escape(str(snapshot['mode']).upper())}</strong> · read-only · <span id="connection">connecting</span> · <span class="freshness {freshness_severity}">{html.escape(newest_text)}</span></p>
{source_banner}
{brain_html}
{strategy_html}
{benchmark_html}
<details class="diagnostic-secondary"><summary>Operational verdict · {html.escape(str(verdict.get('headline', 'unknown')))}</summary>{verdict_html}</details>
<div class="cards">{card_html}</div>
<div class="telemetry">{telemetry_html}</div>
{session_picker}
<h2>Division of labor</h2>{accounting_html}
<h2>Current operations</h2>{operations}
<h2>Run history</h2>
{triage_toolbar}
<h2>Live OpenCode console</h2>
<div class="console-wrapper" id="live-control-console">
  <div class="console-placeholder" id="console-placeholder">
    <button type="button" id="open-console-btn" class="console-open-btn">Open Control Console</button>
  </div>
  <div class="console-panel" id="console-panel" hidden>
    <div class="console-header">
      <h3>Live OpenCode control panel</h3>
      <button type="button" id="close-console-btn" class="console-close-btn" aria-label="Close console">×</button>
    </div>
    <div class="console-connection-form" id="connection-form">
      <p class="console-warning">
        ⚠️ <strong>Sensitive content warning:</strong> Raw prompts, outputs, commands, paths, and secrets may appear in the transcript. Only use this console with trusted operators in a secure environment.
      </p>
      <form id="console-login-form">
        <div class="form-group">
          <label for="console-run-id">Run ID</label>
          <input type="text" id="console-run-id" name="run_id" placeholder="e.g., run-abc123" required aria-describedby="run-id-hint">
          <small id="run-id-hint">Obtain from the dashboard run history or workflow operations</small>
        </div>
        <div class="form-group">
          <label for="console-operator">Operator name</label>
          <input type="text" id="console-operator" name="operator" placeholder="e.g., operator-name" required>
        </div>
        <div class="form-group">
          <label for="console-token">Bearer token</label>
          <input type="password" id="console-token" name="token" placeholder="Bearer TOKEN" required autocomplete="off">
          <small>Token is stored only in sessionStorage and never in localStorage, URL, or logs</small>
        </div>
        <button type="submit" class="console-submit-btn">Connect</button>
      </form>
    </div>
    <div class="console-connected" id="console-connected" hidden>
      <div class="console-status">
        <span class="status-indicator" id="console-status-indicator"></span>
        <span id="console-status-text">Connected</span>
        <button type="button" id="console-disconnect-btn" class="console-disconnect-btn">Disconnect</button>
      </div>
      <div class="console-controls">
        <span class="console-run-info" id="console-run-info"></span>
        <span class="console-session-info" id="console-session-info"></span>
        <div class="console-filters" id="console-filters">
          <label><input type="checkbox" class="filter-checkbox" data-filter="dialogue" checked> Dialogue</label>
          <label><input type="checkbox" class="filter-checkbox" data-filter="tool" checked> Tool</label>
          <label><input type="checkbox" class="filter-checkbox" data-filter="error" checked> Error</label>
        </div>
      </div>
      <div class="console-transcript" id="console-transcript">
        <div class="transcript-empty">No transcript events yet. Waiting for activity...</div>
      </div>
      <div class="transcript-footer">
        <button type="button" id="transcript-autoscroll-btn" class="transcript-toggle-btn" aria-pressed="true">Autoscroll</button>
        <button type="button" id="transcript-clear-btn" class="transcript-clear-btn">Clear</button>
      </div>
      <div class="console-pending-permissions" id="console-pending-permissions">
        <h4>Pending permissions</h4>
        <div class="permissions-list" id="permissions-list"></div>
        <p class="empty-message" id="permissions-empty">No pending permissions</p>
      </div>
      <div class="console-pending-questions" id="console-pending-questions">
        <h4>Pending questions</h4>
        <div class="questions-list" id="questions-list"></div>
        <p class="empty-message" id="questions-empty">No pending questions</p>
      </div>
      <details class="console-policy"><summary>Effective permission policy</summary>
        <p>Server-resolved global and role rules, shown in evaluation order.</p>
        <pre id="console-policy-rules">Unavailable</pre>
      </details>
    </div>
  </div>
</div>
<div class="table-wrap"><table><thead><tr><th class="sortable" data-sort="text">Run</th><th class="sortable" data-sort="text">Role / model</th><th class="sortable" data-sort="text">Task</th><th class="sortable" data-sort="text">Claim</th><th class="sortable" data-sort="text">Observed</th><th class="sortable" data-sort="text">Lifecycle</th><th class="sortable" data-sort="text">Delivery</th><th class="sortable" data-sort="num">Time</th><th class="sortable" data-sort="num">Tokens L/F</th><th>Evidence</th></tr></thead><tbody id="run-history-body">{rows}</tbody></table></div>
<p class="table-empty-note" id="run-empty-note" hidden>No runs match this filter.</p>
<footer>Unknown and unadjudicated are first-class states. A contradiction means observed evidence disagrees with the worker claim.</footer>
</main><script src="/dashboard.js"></script></body></html>"""


def _operation_pair(operation: dict[str, Any] | None, plan: dict[str, Any] | None) -> str:
    workflow_html = _operation(operation) if operation else '<section class="operation"><h3>Current workflow</h3><p class="empty">Workflow unavailable for this plan.</p></section>'
    plan_html = _running_plan(plan) if plan is not None else ""
    return f'<div class="operation-pair">{workflow_html}{plan_html}</div>'


def _running_plan(plan: dict[str, Any] | None) -> str:
    if plan is None:
        return ""
    steps = "".join(
        f'<li class="plan-step {html.escape(step["state"])}"><span class="plan-state">{html.escape(step["state"])}</span><strong>{html.escape(step["summary"])}</strong>{_plan_refs(step.get("evidence_refs", []))}</li>'
        for step in plan["steps"]
    )
    current = plan["current_step"]["summary"] if plan["current_step"] else "complete"
    diagnostics = "".join(
        f'<span class="plan-diagnostic">{html.escape(item["kind"])}</span>' for item in plan["diagnostics"]
    )
    history = "".join(
        f'<li>revision {item["revision"]} · {html.escape(item["state"])} · {html.escape(item["observed_at"])} · {html.escape(item["source"])}{(" · " + html.escape(item["revision_reason"])) if item["revision_reason"] else ""}</li>'
        for item in plan["history"]
    )
    issue = f'issue {plan["issue"]}'
    if plan.get("repository"):
        url = f'https://github.com/{quote(plan["repository"], safe="/")}/issues/{plan["issue"]}'
        issue = f'<a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer">{issue}</a>'
    age = _format_age(plan["age_seconds"]) if plan.get("age_seconds") is not None else "age unavailable"
    changed = "" if plan["revision"] == 1 else (
        f'<p class="plan-change">Plan changed {age}: {html.escape(plan["revision_reason"] or "reason unavailable")}</p>'
    )
    inspect_attrs = _microscope_attrs(
        key=f'plan:{plan.get("session_id")}:{plan.get("issue")}', label="Running plan",
        kind="running-plan", state="active" if plan["current_step"] else "completed",
        observed=plan.get("observed_at"), reason=plan.get("revision_reason") or "durable plan evidence",
        source=plan.get("source"), extra={
            "objective": plan.get("objective"), "repository": plan.get("repository"),
            "issue": plan.get("issue"), "session": plan.get("session_id"),
            "phase": current,
        },
    )
    return (
        '<section class="running-plan">'
        f'<div class="plan-head"><h3>Running plan</h3><span>{plan["completed_steps"]} / {plan["total_steps"]} · revision {plan["revision"]}</span>'
        f'<button type="button" class="microscope-trigger" {inspect_attrs}>Inspect evidence</button></div>'
        f'<p class="plan-objective">{html.escape(plan["objective"])}</p>'
        f'<p><small>{issue} · current: {html.escape(current)} · updated {age} · source {html.escape(plan["source"])}</small></p>'
        f'{changed}{_plan_refs(plan["evidence_refs"])}{diagnostics}<ol class="plan-steps">{steps}</ol>'
        f'<details class="plan-history" data-details-id="{_plan_details_id(plan)}"><summary>Revision history ({len(plan["history"])})</summary><ol>{history}</ol></details>'
        '</section>'
    )


def _plan_details_id(plan: dict[str, Any]) -> str:
    return html.escape("plan:" + "|".join(str(plan.get(key) or "") for key in ("session_id", "repository", "operation_id", "issue")))


def _plan_refs(references: list[str]) -> str:
    if not references:
        return ""
    return '<small class="plan-evidence">' + " · ".join(html.escape(item) for item in references) + "</small>"


def _operation(operation: dict[str, Any]) -> str:
    milestones = "".join(
        f'<div class="milestone {html.escape(m["state"])}"><span class="milestone-name">{html.escape(m["label"])}</span><strong class="milestone-state">{html.escape("not needed" if m["state"] == "not-applicable" else m["state"])}</strong>{_milestone_activities_html(m["activities"])}</div>'
        for m in operation["milestones"]
    )
    rail_dots = "".join(
        f'<span class="rail-dot {html.escape(m["state"])}" title="{html.escape(m["label"])}: {html.escape(m["state"])}"></span>'
        for m in operation["milestones"]
    )
    flow_id = html.escape("flow:" + "|".join(str(operation.get(key) or "") for key in ("session_id", "operation_id", "issue")))
    stop = f' · stopped: {html.escape(str(operation["stop_reason"]))}' if operation["stop_reason"] else ""
    issue_text = f"issue {operation['issue']}"
    title = html.escape(operation["operation_id"])
    if operation.get("repository"):
        issue_url = f"https://github.com/{quote(operation['repository'], safe='/')}/issues/{operation['issue']}"
        issue_text = f'<a href="{html.escape(issue_url)}" target="_blank" rel="noopener noreferrer">{issue_text}</a>'
        title = f'<a href="{html.escape(issue_url)}" target="_blank" rel="noopener noreferrer">{title}</a>'
    session = html.escape(str(operation.get("session_id") or "legacy"))
    diagnostics = "".join(
        f'<span class="workflow-diagnostic">{html.escape(item["kind"])}</span>'
        for item in operation.get("diagnostics", [])
    )
    unassigned = [item for item in operation["activities"] if item["milestone"] == "unassigned"]
    unassigned_html = "" if not unassigned else (
        '<div class="unassigned-activities" aria-label="Unassigned activities"><strong>Unassigned activity</strong>'
        + _milestone_activities_html(unassigned) + "</div>"
    )
    loop = ""
    corrections = [item for item in operation["activities"] if item["activity_type"] == "correction" and item["state"] != "not-applicable"]
    if corrections:
        latest = max(corrections, key=lambda item: (item["correction_cycle"], item["observed_at"]))
        loop = f'<div class="correction-loop" aria-label="Correction loop cycle {latest["correction_cycle"]}">↩ correction cycle {latest["correction_cycle"]} → implementation · {html.escape(latest["state"])}</div>'
    flow_summary = (
        f'<summary class="flow-summary"><span class="rail-dots">{rail_dots}</span>'
        f'<span class="rail-current">{html.escape(operation["current_milestone"])} · {html.escape(operation["current_state"])}</span></summary>'
    )
    flow = f'<details class="flow-toggle" data-details-id="{flow_id}">{flow_summary}<div class="flow" aria-label="Delivery milestones">{milestones}</div></details>'
    inspect_attrs = _microscope_attrs(
        key=f'operation:{operation.get("operation_id")}', label=operation.get("operation_id"),
        kind="workflow-operation", state=operation.get("current_state"),
        observed=operation.get("updated_at"),
        reason=f'{operation.get("current_milestone")} / {operation.get("current_state")}',
        source="workflow-ledger", extra={
            "repository": operation.get("repository"), "issue": operation.get("issue"),
            "session": operation.get("session_id"), "phase": operation.get("current_milestone"),
        },
    )
    inspect = f'<button type="button" class="microscope-trigger" {inspect_attrs}>Inspect evidence</button>'
    return f'<section class="operation"><h3>Current workflow</h3><div class="operation-head"><strong>{title}</strong><span>{issue_text} · session {session} · {html.escape(operation["current_milestone"])} / {html.escape(operation["current_state"])}{stop}</span>{inspect}</div>{diagnostics}{flow}{unassigned_html}{loop}</section>'


def _milestone_activities_html(activities: list[dict[str, Any]]) -> str:
    if not activities:
        return ""
    activity_html = " ".join(
        f'<span class="milestone-activity {html.escape(item["state"])}">{html.escape(item["activity_type"])} · {html.escape("not needed" if item["state"] == "not-applicable" else item["state"])}</span>'
        for item in activities
    )
    return f'<div class="milestone-activities">{activity_html}</div>'


def _session_label(session_id: str, sample: dict[str, Any]) -> str:
    """Give the session picker something a human can tell apart at a glance."""
    if session_id == "legacy":
        return "Legacy (no session ID)"
    repository = sample.get("repository")
    issue = sample.get("issue")
    if repository and issue:
        return f"{repository}#{issue}"
    if issue:
        return f"issue {issue}"
    operation_id = sample.get("operation_id")
    if operation_id:
        return str(operation_id)
    return session_id[:8]


def _derive_sessions(
    operations: list[dict[str, Any]], now: datetime,
    plans: tuple[dict[str, Any], ...] = (),
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for operation in operations:
        grouped.setdefault(operation["session_id"] or "legacy", []).append(operation)
    sessions = []
    for session_id, items in grouped.items():
        newest = max(items, key=lambda item: item["updated_at"])
        states = {(item["current_stage"], item["current_state"]) for item in items}
        if any(state == "active" for _, state in states):
            status = "active"
        elif states and all(stage == "post-merge" and state == "passed" for stage, state in states):
            status = "completed"
        else:
            status = "idle"
        sessions.append({
            "session_id": session_id, "status": status, "updated_at": newest["updated_at"],
            "operations": len(items), "label": _session_label(session_id, newest),
        })
    known = set(grouped)
    plan_groups: dict[str, list[dict[str, Any]]] = {}
    for plan in plans:
        session_id = plan["session_id"] or "legacy"
        if session_id not in known:
            plan_groups.setdefault(session_id, []).append(plan)
    for session_id, items in plan_groups.items():
        newest = max(items, key=lambda item: item["observed_at"])
        status = "active" if newest["current_step"] is not None else "completed"
        sessions.append({
            "session_id": session_id, "status": status,
            "updated_at": newest["observed_at"], "operations": 0,
            "label": _session_label(session_id, newest),
        })
    return sorted(sessions, key=lambda item: (item["updated_at"], item["session_id"]), reverse=True)


def _accounting(
    runs: tuple[Any, ...], views: tuple[RunView, ...],
    usage: tuple[dict[str, Any], ...], operations: list[dict[str, Any]],
) -> dict[str, Any]:
    observed = [item for item in usage if item["status"] == "observed"]
    local_input = sum(item.usage.local_input for item in runs)
    local_cached_values = [item.usage.local_cache_read for item in runs if item.usage.cache_provenance == "observed"]
    local_generated = sum(item.usage.local_output + item.usage.local_reasoning for item in runs)
    local_cache_complete = bool(runs) and len(local_cached_values) == len(runs)
    codex_complete = bool(usage) and len(observed) == len(usage)
    codex_input = sum(item["input_tokens"] for item in observed) if codex_complete else None
    codex_cached = sum(item["cached_input_tokens"] for item in observed) if codex_complete else None
    codex_generated = sum(item["output_tokens"] + item["reasoning_output_tokens"] for item in observed) if codex_complete else None
    return {
        "local_input": local_input,
        "local_cached": sum(value or 0 for value in local_cached_values) if local_cache_complete else None,
        "local_uncached": local_input - sum(value or 0 for value in local_cached_values) if local_cache_complete else None,
        "local_cache_provenance": "observed" if local_cache_complete else "unsupported",
        "local_generated": local_generated, "codex_input": codex_input,
        "codex_cached": codex_cached, "codex_generated": codex_generated,
        "codex_uncached": sum(item["uncached_input_tokens"] for item in observed) if codex_complete else None,
        "codex_cache_provenance": "observed" if codex_complete else "unsupported",
        "codex_usage_operations": len(observed), "worker_runs": len(runs),
        "codex_interventions": sum(item.intervention_depth != "none" for item in views),
        "corrections": sum(item.get("correction_cycle", 0) for item in operations),
        "takeovers": sum(item.intervention_depth == "takeover" for item in views),
    }


def _sparkline_svg(values: list[float], label: str, width: int = 150, height: int = 30) -> str:
    finite = [value for value in values if value is not None]
    if len(finite) < 2 or max(finite) <= 0:
        return ""
    vmax, vmin = max(finite), min(finite)
    span = (vmax - vmin) or 1
    step = width / (len(finite) - 1)
    points = [
        (i * step, height - ((value - vmin) / span) * (height - 4) - 2)
        for i, value in enumerate(finite)
    ]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area = f"0,{height} {line} {width},{height}"
    last_x, last_y = points[-1]
    return (
        f'<svg class="sparkline" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" aria-label="{html.escape(label)}">'
        f'<polygon points="{area}" fill="var(--line)" opacity="0.6"></polygon>'
        f'<polyline points="{line}" fill="none" stroke="var(--good)" stroke-width="1.5"></polyline>'
        f'<circle cx="{last_x:.1f}" cy="{last_y:.1f}" r="2.2" fill="var(--good)"></circle>'
        "</svg>"
    )


def _accounting_html(accounting: dict[str, Any], views: list[dict[str, Any]]) -> str:
    processed = accounting["local_input"] + accounting["codex_input"] if accounting["codex_input"] is not None else None
    generated = accounting["local_generated"] + accounting["codex_generated"] if accounting["codex_generated"] is not None else None
    local_processed_share = f'{accounting["local_input"] / processed * 100:.1f}%' if processed else "Not available"
    local_generated_share = f'{accounting["local_generated"] / generated * 100:.1f}%' if generated else "Not available"
    cached = (
        f'{accounting["local_cached"]:,} (observed)'
        if accounting["local_cache_provenance"] == "observed" else "Not reported (unsupported)"
    )
    codex_cached = (
        f'{accounting["codex_cached"]:,} (observed)'
        if accounting["codex_cache_provenance"] == "observed" else "Not reported (unsupported)"
    )
    uncached = (
        f'local {accounting["local_uncached"]:,} / frontier {accounting["codex_uncached"]:,}'
        if accounting["local_uncached"] is not None and accounting["codex_uncached"] is not None
        else "Not fully reported"
    )
    recent = views[-24:]
    spark = _sparkline_svg(
        [view["local_tokens"] for view in recent],
        f"Local tokens across the last {len(recent)} runs",
    )
    spark_html = f'<div class="card-spark">{spark}</div>' if spark else ""
    footnote = (
        '<p class="accounting-footnote">Cached input (not shown as a card above — local worker '
        f'profiles do not all report it yet): local {cached} · frontier {codex_cached} · uncached {uncached}</p>'
    )
    return (
        '<div class="cards">'
        f'<section class="card"><span>Processed context · local share</span><strong>{local_processed_share}</strong><small>local {accounting["local_input"]:,} / frontier {accounting["codex_input"] if accounting["codex_input"] is not None else "not reported"}</small>{spark_html}</section>'
        f'<section class="card"><span>Generated + reasoning · local share</span><strong>{local_generated_share}</strong><small>local {accounting["local_generated"]:,} / frontier {accounting["codex_generated"] if accounting["codex_generated"] is not None else "not reported"}</small></section>'
        f'<section class="card"><span>Operational contribution</span><strong>{accounting["worker_runs"]} local runs</strong><small>{accounting["codex_interventions"]} Codex interventions · {accounting["corrections"]} corrections · {accounting["takeovers"]} takeovers</small></section>'
        f'</div>{footnote}'
    )


def _run_row(view: dict[str, Any]) -> str:
    css = "contradiction" if view["contradiction"] else ""
    outcome = html.escape(str(view["effective_outcome"]))
    evidence = "<br>".join(html.escape(str(item)) for item in view["evidence_refs"]) or "—"
    role_model = f"{html.escape(str(view['role'] or 'unknown'))}<br><code>{html.escape(str(view['model'] or 'unknown'))}</code>"
    triage = _triage_bucket(view)
    search_fields = (
        view["run_id"], view["issue"], view["role"], view["model"], view["task_class"],
        view["claimed_status"], view["effective_outcome"], view["lifecycle_state"],
    )
    search = html.escape(" ".join(str(field).lower() for field in search_fields if field is not None))
    run_id = html.escape(str(view["run_id"]))
    inspect_attrs = _microscope_attrs(
        key=f'run:{view["run_id"]}', label=f'Run {view["run_id"]}', kind="run",
        state=view["effective_outcome"], observed=None,
        reason=f'claim {view["claimed_status"] or "none"}; observed {view["effective_outcome"]}',
        source=" · ".join(str(item) for item in view["evidence_refs"]), extra={
            "run": view["run_id"], "role": view["role"], "model": view["model"],
            "issue": view["issue"], "phase": view["lifecycle_state"],
            "elapsed": view["wall_seconds"], "tokens": view["local_tokens"],
        },
    )
    return (
        f'<tr class="{css}" data-triage="{triage}" data-search="{search}"><td><code>{run_id}</code><br>issue {view["issue"] or "—"}'
        f'<br><button type="button" class="microscope-trigger" {inspect_attrs}>Inspect</button></td>'
        f'<td>{role_model}</td><td>{html.escape(str(view["task_class"] or "unknown"))}</td>'
        f'<td>{html.escape(str(view["claimed_status"] or "none"))}</td><td class="{outcome}">{outcome}</td>'
        f'<td>{html.escape(str(view["lifecycle_state"]))}</td><td>{html.escape(str(view["delivery_ready"]))}</td>'
        f'<td data-value="{view["wall_seconds"]}">{view["wall_seconds"]:.1f}s</td><td data-value="{view["local_tokens"]}">{view["local_tokens"]:,} / {view["frontier_tokens"]:,}</td>'
        f'<td><details data-details-id="run:{run_id}"><summary>{len(view["evidence_refs"])} refs</summary>{evidence}</details></td></tr>'
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the local read-only harness diagnostics dashboard")
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--adjudications", type=Path, required=True)
    parser.add_argument("--lifecycle", type=Path, required=True)
    parser.add_argument("--workflow", type=Path)
    parser.add_argument("--frontier", type=Path)
    parser.add_argument("--publication-failures", type=Path)
    parser.add_argument("--operation-usage", type=Path)
    parser.add_argument("--plans", type=Path)
    parser.add_argument("--v5-runs", type=Path)
    parser.add_argument("--annotations", type=Path)
    parser.add_argument("--control-token-file", type=Path)
    parser.add_argument("--control-audit-file", type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--release", default="v0.2.0")
    parser.add_argument("--mode", choices=("live", "replay"), default="live")
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("dashboard must bind to a loopback address")
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    source = DashboardSource(
        args.runs,
        args.adjudications,
        args.lifecycle,
        args.workflow,
        args.frontier,
        args.publication_failures,
        args.release,
        args.mode,
        operation_usage=args.operation_usage,
        plans=args.plans,
        v5_runs=args.v5_runs,
        annotations=args.annotations,
    )
    if bool(args.control_token_file) != bool(args.control_audit_file):
        parser.error("control token and audit files must be configured together")
    broker = None
    if args.control_token_file:
        if not args.v5_runs:
            parser.error("control plane requires --v5-runs")
        broker = ControlBroker(args.v5_runs, args.control_token_file, args.control_audit_file)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(source, broker))
    print(f"Harness dashboard: http://{args.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


DASHBOARD_JS = """(() => {
  let initial = true;
  let activeFilter = null;
  let searchTerm = '';
  let sortState = null;
  let selectedBrainKey = null;
  const openDetailIds = new Set();

  const events = new EventSource('/events' + window.location.search);
  const connection = (text, color) => {
    const state = document.getElementById('connection');
    if (state) { state.textContent = text; state.style.color = color; }
  };

  const countByTriage = (value) =>
    document.querySelectorAll('#run-history-body tr[data-triage="' + value + '"]').length;

  const applyFilters = () => {
    const tbody = document.getElementById('run-history-body');
    if (!tbody) { return; }
    const term = searchTerm.trim().toLowerCase();
    let visible = 0;
    tbody.querySelectorAll('tr[data-triage]').forEach((row) => {
      const matchesFilter = activeFilter === 'all' || row.dataset.triage === activeFilter;
      const matchesSearch = !term || (row.dataset.search || '').includes(term);
      const show = matchesFilter && matchesSearch;
      row.style.display = show ? '' : 'none';
      if (show) { visible += 1; }
    });
    const note = document.getElementById('run-empty-note');
    if (note) { note.hidden = visible !== 0; }
    document.querySelectorAll('.triage-tab').forEach((tab) => {
      tab.classList.toggle('active', tab.dataset.filter === activeFilter);
    });
  };

  const initFilter = () => {
    if (activeFilter === null) {
      activeFilter = countByTriage('needs-judgment') > 0 ? 'needs-judgment'
        : countByTriage('contradiction') > 0 ? 'contradiction' : 'all';
    }
    applyFilters();
  };

  const sortRows = () => {
    if (!sortState) { return; }
    const tbody = document.getElementById('run-history-body');
    const table = tbody && tbody.closest('table');
    if (!tbody || !table) { return; }
    table.querySelectorAll('th.sortable').forEach((th, index) => {
      th.classList.remove('sort-asc', 'sort-desc');
      if (index === sortState.index) { th.classList.add(sortState.dir === 1 ? 'sort-asc' : 'sort-desc'); }
    });
    const rows = Array.from(tbody.querySelectorAll('tr[data-triage]'));
    rows.sort((a, b) => {
      const cellA = a.children[sortState.index];
      const cellB = b.children[sortState.index];
      const rawA = (cellA.dataset.value !== undefined) ? cellA.dataset.value : cellA.textContent.trim();
      const rawB = (cellB.dataset.value !== undefined) ? cellB.dataset.value : cellB.textContent.trim();
      if (sortState.type === 'num') { return (parseFloat(rawA) - parseFloat(rawB)) * sortState.dir; }
      return rawA.localeCompare(rawB) * sortState.dir;
    });
    rows.forEach((row) => tbody.appendChild(row));
  };

  const applyState = () => {
    initFilter();
    sortRows();
    document.querySelectorAll('.run-filter').forEach((input) => { input.value = searchTerm; });
    document.querySelectorAll('details[data-details-id]').forEach((el) => {
      if (openDetailIds.has(el.dataset.detailsId)) { el.open = true; }
    });
    const choices = Array.from(document.querySelectorAll('[data-brain-key]'));
    const selected = choices.find((el) => el.dataset.brainKey === selectedBrainKey)
      || choices.find((el) => el.classList.contains('state-running-tests') || el.classList.contains('state-waiting-tool') || el.classList.contains('state-waiting-inference'))
      || choices[0];
    if (selected) { showBrainDetail(selected); }
  };

  const showBrainDetail = (choice) => {
    selectedBrainKey = choice.dataset.brainKey;
    document.querySelectorAll('[data-brain-key]').forEach((item) => {
      item.classList.toggle('brain-select-active', item.dataset.brainKey === selectedBrainKey);
      item.setAttribute('aria-pressed', item.dataset.brainKey === selectedBrainKey ? 'true' : 'false');
    });
    const fields = {
      'brain-detail-title': choice.dataset.label,
      'brain-detail-kind': choice.dataset.kind,
      'brain-detail-state': choice.dataset.state,
      'brain-detail-age': choice.dataset.age + ' · ' + choice.dataset.observed,
      'brain-detail-reason': choice.dataset.reason,
      'brain-detail-source': choice.dataset.source,
      'brain-detail-run': (choice.dataset.run || 'Unavailable') + ' · ' + (choice.dataset.phase || 'phase unavailable'),
      'brain-detail-role': (choice.dataset.role || 'Unavailable') + ' · ' + (choice.dataset.model || 'model unavailable'),
      'brain-detail-identity': (choice.dataset.repository || 'repository unavailable') + ' · issue ' + (choice.dataset.issue || 'unknown') + ' · session ' + (choice.dataset.session || 'unknown'),
      'brain-detail-objective': choice.dataset.objective || 'Unavailable',
      'brain-detail-elapsed': choice.dataset.elapsed && choice.dataset.elapsed !== 'Unavailable' ? choice.dataset.elapsed + ' seconds' : 'Unavailable',
      'brain-detail-activity': 'tools ' + (choice.dataset.tools || 'unknown') + ' · errors ' + (choice.dataset.toolErrors || '0') + ' · tests ' + (choice.dataset.tests || 'unknown'),
      'brain-detail-files': choice.dataset.files || 'Unavailable',
      'brain-detail-durable': choice.dataset.durable || 'Unavailable',
      'brain-detail-tokens': choice.dataset.tokens || 'Unavailable',
    };
    Object.entries(fields).forEach(([id, value]) => {
      const target = document.getElementById(id);
      if (target) { target.textContent = value || 'Unavailable'; }
    });
    if (choice.classList.contains('microscope-trigger')) {
      const inspector = document.querySelector('.brain-inspector');
      if (inspector) { inspector.scrollIntoView({block: 'nearest'}); }
    }
  };

  document.addEventListener('click', (event) => {
    const brainChoice = event.target.closest('[data-brain-key]');
    if (brainChoice) {
      showBrainDetail(brainChoice);
      const runId = brainChoice.dataset.run;
      if (runId && consolePlaceholder) {
        openConsole();
        const runInput = document.getElementById('console-run-id');
        if (runInput) { runInput.value = runId; }
      }
      return;
    }
    const tab = event.target.closest('.triage-tab');
    if (tab) { activeFilter = tab.dataset.filter; applyFilters(); return; }
    const header = event.target.closest('th.sortable');
    if (header) {
      const index = Array.from(header.parentElement.children).indexOf(header);
      const type = header.dataset.sort === 'num' ? 'num' : 'text';
      const dir = (sortState && sortState.index === index) ? sortState.dir * -1 : 1;
      sortState = {index, dir, type};
      sortRows();
    }
  });

  document.addEventListener('input', (event) => {
    if (event.target.classList.contains('run-filter')) {
      searchTerm = event.target.value;
      applyFilters();
    }
  });

  document.addEventListener('change', (event) => {
    if (event.target.id === 'session-select' && event.target.form) {
      event.target.form.submit();
    }
  });

  document.addEventListener('toggle', (event) => {
    const details = event.target;
    if (details && details.dataset && details.dataset.detailsId) {
      if (details.open) { openDetailIds.add(details.dataset.detailsId); }
      else { openDetailIds.delete(details.dataset.detailsId); }
    }
  }, true);

  const refresh = async () => {
    const scrollY = window.scrollY;
    const response = await fetch('/' + window.location.search, {cache: 'no-store'});
    if (!response.ok) { throw new Error('dashboard refresh failed'); }
    const parsed = new DOMParser().parseFromString(await response.text(), 'text/html');
    const current = document.querySelector('main');
    const replacement = parsed.querySelector('main');
    const liveConsole = document.getElementById('live-control-console');
    const replacementConsole = replacement && replacement.querySelector('#live-control-console');
    if (liveConsole && replacementConsole) { replacementConsole.replaceWith(liveConsole); }
    if (current && replacement) { current.replaceWith(replacement); }
    applyState();
    window.scrollTo(0, scrollY);
    connection('live', 'var(--good)');
  };
  events.addEventListener('open', () => connection('live', 'var(--good)'));
  events.addEventListener('snapshot', () => {
    if (initial) { initial = false; } else { refresh().catch(() => connection('stale', 'var(--bad)')); }
  });
  events.addEventListener('error', () => connection('disconnected', 'var(--bad)'));

  // ===================== CONTROL CONSOLE =====================
  let consoleRunning = false;
  let consoleToken = null;
  let consoleOperator = null;
  let consoleRunId = null;
  let consoleSessionId = null;
  let consolePoller = null;
  let consoleConnected = false;
  let consoleAutoscroll = true;
  let consoleFilters = { dialogue: true, tool: true, error: true };

  const openConsoleBtn = document.getElementById('open-console-btn');
  const closeConsoleBtn = document.getElementById('close-console-btn');
  const consolePanel = document.getElementById('console-panel');
  const consolePlaceholder = document.getElementById('console-placeholder');
  const connectionForm = document.getElementById('console-login-form');
  const consoleConnectedSection = document.getElementById('console-connected');
  const consoleRunInfo = document.getElementById('console-run-info');
  const consoleSessionInfo = document.getElementById('console-session-info');
  const consoleStatusText = document.getElementById('console-status-text');
  const statusIndicator = document.getElementById('console-status-indicator');
  const consoleTranscript = document.getElementById('console-transcript');
  const transcriptAutoscrollBtn = document.getElementById('transcript-autoscroll-btn');
  const transcriptClearBtn = document.getElementById('transcript-clear-btn');
  const consoleFiltersContainer = document.getElementById('console-filters');
  const consoleDisconnectBtn = document.getElementById('console-disconnect-btn');
  const permissionsList = document.getElementById('permissions-list');
  const permissionsEmpty = document.getElementById('permissions-empty');
  const questionsList = document.getElementById('questions-list');
  const questionsEmpty = document.getElementById('questions-empty');
  const consolePolicyRules = document.getElementById('console-policy-rules');

  const openConsole = () => {
    consolePlaceholder.hidden = true;
    consolePanel.hidden = false;
    // Check if we already have a stored token and run ID
    const storedToken = sessionStorage.getItem('consoleToken');
    const storedRunId = sessionStorage.getItem('consoleRunId');
    if (storedToken && storedRunId) {
      // Auto-connect if we have stored credentials
      connectionForm.hidden = true;
      consoleConnectedSection.hidden = false;
      connectConsole(storedRunId, storedToken);
    } else {
      connectionForm.hidden = false;
      consoleConnectedSection.hidden = true;
    }
  };

  const closeConsole = () => {
    disconnectConsole();
    consolePanel.hidden = true;
    consolePlaceholder.hidden = false;
  };

  const disconnectConsole = () => {
    consoleConnected = false;
    consoleRunning = false;
    consoleToken = null;
    consoleOperator = null;
    consoleRunId = null;
    consoleSessionId = null;
    if (consolePoller) {
      clearInterval(consolePoller);
      consolePoller = null;
    }
    // Don't clear sessionStorage here - keep token for convenience
    connectionForm.hidden = false;
    consoleConnectedSection.hidden = true;
    connectionForm.reset();
  };

  const updateStatus = (text, state) => {
    consoleStatusText.textContent = text;
    if (state === 'connected') {
      statusIndicator.className = 'status-indicator connected';
    } else if (state === 'error') {
      statusIndicator.className = 'status-indicator error';
    } else if (state === 'loading') {
      statusIndicator.className = 'status-indicator loading';
    } else {
      statusIndicator.className = 'status-indicator';
    }
  };

  const connectConsole = (runId, token, operatorName) => {
    consoleRunId = runId;
    consoleToken = token;
    consoleOperator = operatorName || 'operator';

    connectionForm.hidden = true;
    consoleConnectedSection.hidden = false;
    consoleConnected = true;
    consoleRunning = true;

    updateStatus('Connecting...', 'loading');

    // Keep credentials only for this browser tab.
    sessionStorage.setItem('consoleToken', consoleToken);
    sessionStorage.setItem('consoleRunId', consoleRunId);

    // Start polling
    if (consolePoller) clearInterval(consolePoller);
    consolePoller = setInterval(pollConsoleSnapshot, 1000);

    // Initial poll
    pollConsoleSnapshot();

    // If brain/worker trigger clicked, scroll to console
    if (consolePanel.querySelector('.brain-node') || consolePanel.querySelector('[data-brain-key]')) {
      setTimeout(() => {
        consolePanel.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 100);
    }
  };

  const pollConsoleSnapshot = async () => {
    if (!consoleConnected || !consoleRunId) return;

    updateStatus('Updating...', 'loading');

    try {
      const response = await fetch(`/api/control/runs/${encodeURIComponent(consoleRunId)}`, {
        method: 'GET',
        headers: {
          'Authorization': `Bearer ${consoleToken}`,
          'X-Operator': consoleOperator
        }
      });

      if (!response.ok) {
        if (response.status === 401 || response.status === 403) {
          updateStatus('Unauthorized - invalid token', 'error');
          consoleConnected = false;
          return;
        }
        if (response.status === 404) {
          updateStatus('Run not found', 'error');
          consoleConnected = false;
          return;
        }
        if (response.status === 409) {
          updateStatus('Run not controllable', 'error');
          consoleConnected = false;
          return;
        }
        updateStatus('Connection error', 'error');
        return;
      }

      const data = await response.json();

      // Store session ID for later use
      if (data.session_id) {
        consoleSessionId = data.session_id;
        const label = document.createElement('strong');
        label.textContent = 'Session: ';
        consoleSessionInfo.replaceChildren(label, document.createTextNode(data.session_id));
      }

      // Update connection status
      if ((data.pending_permissions || []).length) {
        updateStatus('Waiting for permission', 'loading');
      } else if ((data.pending_questions || []).length) {
        updateStatus('Waiting for answer', 'loading');
      } else if (data.state === 'active' || data.state === 'running') {
        updateStatus('Generating / active', 'connected');
      } else if (data.state === 'completed') {
        updateStatus('Completed', 'connected');
      } else if (data.state === 'failed') {
        updateStatus('Failed', 'error');
      } else {
        updateStatus(`Running (${data.state || 'unknown'})`, 'connected');
      }

      // Render transcript events
      renderTranscriptEvents(data.events || [], data.events_truncated);

      // Render pending permissions
      renderPendingPermissions(data.pending_permissions || []);

      // Render pending questions
      renderPendingQuestions(data.pending_questions || []);
      consolePolicyRules.textContent = JSON.stringify(data.effective_policy || {source: 'unavailable'}, null, 2);

    } catch (err) {
      updateStatus('Disconnected', 'error');
      consoleConnected = false;
    }
  };

  const renderTranscriptEvents = (events, truncated) => {
    const container = consoleTranscript;
    container.replaceChildren();

    // Apply filters
    const filteredEvents = events.filter(event => {
      const kind = String(event.kind || event.type || 'unknown').toLowerCase();
      return consoleFilters.dialogue && (kind.includes('dialogue') || kind.includes('response') || kind === 'text' || kind === 'reasoning') ||
             consoleFilters.tool && (kind.includes('tool') || kind.includes('action')) ||
             consoleFilters.error && (kind.includes('error') || kind.includes('exception'));
    });

    if (filteredEvents.length === 0) {
      const empty = document.createElement('div');
      empty.className = 'transcript-empty';
      empty.textContent = 'No matching transcript events yet.';
      container.appendChild(empty);
      return;
    }

    filteredEvents.forEach(event => {
      const rawTime = event.observed_at || event.timestamp || '';
      const time = typeof rawTime === 'number' ? new Date(rawTime).toLocaleTimeString() : rawTime;
      const type = event.kind || event.type || 'unknown';
      const content = event.part || event.content || event;

      const div = document.createElement('div');
      div.className = `event ${type}`;

      // Construct all event-derived content as DOM text nodes.
      const timeEl = document.createElement('span');
      timeEl.className = 'event-time';
      timeEl.textContent = time || 'unknown time';

      const contentEl = document.createElement('span');
      contentEl.className = 'event-content';

      // Extract text content safely
      if (typeof content === 'string') {
        contentEl.textContent = content;
      } else if (content && typeof content === 'object') {
        // Try to extract text from common formats
        const text = content.text || content.content || JSON.stringify(content, null, 2);
        contentEl.textContent = text;
      } else {
        contentEl.textContent = JSON.stringify(content, null, 2);
      }

      div.appendChild(timeEl);
      div.appendChild(contentEl);
      container.appendChild(div);
    });

    // Autoscroll
    if (consoleAutoscroll) {
      container.scrollTop = container.scrollHeight;
    }

    // Show truncation indicator
    if (truncated) {
      const note = document.createElement('div');
      note.className = 'transcript-empty';
      note.textContent = '… (transcript truncated)';
      container.appendChild(note);
    }
  };

  const renderPendingPermissions = (permissions) => {
    permissionsList.replaceChildren();

    if (permissions.length === 0) {
      permissionsEmpty.hidden = false;
      return;
    }

    permissionsEmpty.hidden = true;

    permissions.forEach(permission => {
      const card = document.createElement('div');
      card.className = 'permission-card';

      const idEl = document.createElement('div');
      idEl.className = 'permission-id';
      idEl.textContent = `Permission request ${permission.id || 'unknown'}`;

      const headerEl = document.createElement('div');
      headerEl.className = 'permission-header';
      headerEl.textContent = permission.permission || 'unknown permission type';

      const patternsEl = document.createElement('div');
      patternsEl.className = 'permission-pattern';
      patternsEl.textContent = `Pattern: ${Array.isArray(permission.patterns) ? permission.patterns.join(', ') : String(permission.patterns || 'any')}`;

      const alwaysPatterns = permission.always;
      if (alwaysPatterns && alwaysPatterns.length > 0) {
        const alwaysEl = document.createElement('div');
        alwaysEl.className = 'permission-pattern';
        alwaysEl.textContent = `Proposed durable pattern: ${Array.isArray(alwaysPatterns) ? alwaysPatterns.join(', ') : String(alwaysPatterns)}`;
        card.appendChild(alwaysEl);
      }
      const evaluationEl = document.createElement('div');
      evaluationEl.className = 'permission-pattern';
      const evaluations = permission.policy_evaluation || [];
      evaluationEl.textContent = evaluations.length
        ? `Winning rule: ${evaluations.map((item) => item.winner ? `${item.winner.layer} ${item.winner.pattern} → ${item.winner.effect}` : `${item.resource} → no match`).join('; ')}`
        : 'Winning rule: unavailable';
      card.appendChild(evaluationEl);

      const actionsEl = document.createElement('div');
      actionsEl.className = 'permission-actions';

      const onceBtn = document.createElement('button');
      onceBtn.className = 'console-btn once';
      onceBtn.textContent = 'Once';
      onceBtn.onclick = () => handlePermissionDecision(permission.id, 'once');

      const alwaysBtn = document.createElement('button');
      alwaysBtn.className = 'console-btn always';
      alwaysBtn.textContent = 'Always';
      alwaysBtn.onclick = () => handlePermissionAlways(permission);

      const rejectBtn = document.createElement('button');
      rejectBtn.className = 'console-btn reject';
      rejectBtn.textContent = 'Reject';
      rejectBtn.onclick = () => handlePermissionDecision(permission.id, 'reject');

      actionsEl.appendChild(onceBtn);
      actionsEl.appendChild(alwaysBtn);
      actionsEl.appendChild(rejectBtn);

      card.appendChild(idEl);
      card.appendChild(headerEl);
      card.appendChild(patternsEl);
      card.appendChild(actionsEl);
      permissionsList.appendChild(card);
    });
  };

  const handlePermissionAlways = (permission) => {
    const patterns = permission.always || permission.patterns;
    const patternText = Array.isArray(patterns) ? patterns.join(', ') : String(patterns || 'pattern not specified');

    if (confirm(`Approve this pattern permanently?\n\nPattern: ${patternText}\n\nThis will create a durable rule allowing this action.`)) {
      handlePermissionDecision(permission.id, 'always');
    }
  };

  const handlePermissionDecision = (permissionId, decision) => {
    if (!consoleRunId || !consoleToken) return;

    const btns = permissionsList.querySelectorAll('button');
    btns.forEach(btn => btn.disabled = true);

    fetch(`/api/control/runs/${encodeURIComponent(consoleRunId)}/permissions/${encodeURIComponent(permissionId)}`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${consoleToken}`,
        'Content-Type': 'application/json',
        'X-Operator': consoleOperator
      },
      body: JSON.stringify({ decision })
    }).then(async (response) => {
      if (!response.ok) {
        throw new Error('Permission decision failed');
      }
      await response.json();
      // Refresh snapshot to show updated state
      pollConsoleSnapshot();
    }).catch((err) => {
      alert(`Failed to submit decision: ${err.message}`);
    }).finally(() => {
      btns.forEach(btn => btn.disabled = false);
    });
  };

  const renderPendingQuestions = (questions) => {
    questionsList.replaceChildren();

    if (questions.length === 0) {
      questionsEmpty.hidden = false;
      return;
    }

    questionsEmpty.hidden = true;

    questions.forEach(question => {
      const card = document.createElement('div');
      card.className = 'question-card';

      const idEl = document.createElement('div');
      idEl.className = 'question-id';
      idEl.textContent = `Question request ${question.id || 'unknown'}`;

      const questionContainer = document.createElement('div');
      questionContainer.className = 'question-options';

      // Create answer buttons for each question
      const questionItems = question.questions || [];
      questionContainer.dataset.questionCount = questionItems.length;

      questionItems.forEach((q, qIndex) => {
        const item = document.createElement('div');
        item.className = 'question-item';
        item.dataset.qIndex = String(qIndex);
        const header = document.createElement('div');
        header.className = 'question-header';
        header.textContent = q.question || q.header || 'Question';

        const optionsDiv = document.createElement('div');
        optionsDiv.className = 'question-options';

        const options = q.options || [];
        options.forEach(opt => {
          const btn = document.createElement('button');
          btn.className = 'console-btn question-select';
          btn.textContent = opt.label || 'Option';
          btn.dataset.qIndex = qIndex;
          btn.dataset.multiple = String(Boolean(q.multiple));
          btn.onclick = () => toggleQuestionOption(btn);
          optionsDiv.appendChild(btn);
        });

        item.appendChild(header);
        item.appendChild(optionsDiv);
        questionContainer.appendChild(item);
      });

      card.appendChild(idEl);
      card.appendChild(questionContainer);

      const actionsEl = document.createElement('div');
      actionsEl.className = 'question-actions';

      const submitBtn = document.createElement('button');
      submitBtn.className = 'console-btn submit-question';
      submitBtn.textContent = 'Submit Answer';
      submitBtn.onclick = () => submitQuestionAnswers(question.id, card);

      const rejectBtn = document.createElement('button');
      rejectBtn.className = 'console-btn reject-question';
      rejectBtn.textContent = 'Reject';
      rejectBtn.onclick = () => rejectQuestion(question.id);

      actionsEl.appendChild(submitBtn);
      actionsEl.appendChild(rejectBtn);

      card.appendChild(actionsEl);
      questionsList.appendChild(card);
    });
  };

  const toggleQuestionOption = (btn) => {
    if (btn.dataset.multiple !== 'true') {
      btn.closest('.question-item').querySelectorAll('.question-select.selected').forEach((choice) => {
        if (choice !== btn) choice.classList.remove('selected');
      });
    }
    btn.classList.toggle('selected');
  };

  const submitQuestionAnswers = (questionId, card) => {
    const items = Array.from(card.querySelectorAll('.question-item'));
    const answers = items.map((item) => Array.from(item.querySelectorAll('.question-select.selected'), (choice) => choice.textContent));
    const missing = answers.findIndex((answer) => answer.length === 0);
    if (missing !== -1) { alert(`Please select at least one option for question ${missing + 1}`); return; }

    if (!consoleRunId || !consoleToken) return;

    const btns = questionsList.querySelectorAll('button');
    btns.forEach(btn => btn.disabled = true);

    fetch(`/api/control/runs/${encodeURIComponent(consoleRunId)}/questions/${encodeURIComponent(questionId)}`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${consoleToken}`,
        'Content-Type': 'application/json',
        'X-Operator': consoleOperator
      },
      body: JSON.stringify({ answers })
    }).then(async (response) => {
      if (!response.ok) {
        throw new Error('Question answer failed');
      }
      await response.json();
      // Refresh snapshot to show updated state
      pollConsoleSnapshot();
    }).catch((err) => {
      alert(`Failed to submit answers: ${err.message}`);
    }).finally(() => {
      btns.forEach(btn => btn.disabled = false);
    });
  };

  const rejectQuestion = (questionId) => {
    if (!consoleRunId || !consoleToken) return;

    if (!confirm('Reject this question request?')) return;

    const btns = questionsList.querySelectorAll('button');
    btns.forEach(btn => btn.disabled = true);

    fetch(`/api/control/runs/${encodeURIComponent(consoleRunId)}/questions/${encodeURIComponent(questionId)}`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${consoleToken}`,
        'Content-Type': 'application/json',
        'X-Operator': consoleOperator
      },
      body: JSON.stringify({ decision: 'reject' })
    }).then(async (response) => {
      if (!response.ok) {
        throw new Error('Question rejection failed');
      }
      await response.json();
      pollConsoleSnapshot();
    }).catch((err) => {
      alert(`Failed to reject question: ${err.message}`);
    }).finally(() => {
      btns.forEach(btn => btn.disabled = false);
    });
  };

  // Event listeners for console controls
  if (openConsoleBtn) {
    openConsoleBtn.addEventListener('click', openConsole);
  }

  if (closeConsoleBtn) {
    closeConsoleBtn.addEventListener('click', closeConsole);
  }

  if (consoleDisconnectBtn) {
    consoleDisconnectBtn.addEventListener('click', disconnectConsole);
  }

  if (connectionForm) {
    connectionForm.addEventListener('submit', (e) => {
      e.preventDefault();
      const runId = document.getElementById('console-run-id').value.trim();
      const token = document.getElementById('console-token').value.trim();
      const operator = document.getElementById('console-operator').value.trim() || 'operator';

      if (!runId || !token) {
        alert('Please provide run ID, operator name, and token');
        return;
      }

      connectConsole(runId, token, operator);
    });
  }

  if (transcriptAutoscrollBtn) {
    transcriptAutoscrollBtn.addEventListener('click', () => {
      consoleAutoscroll = !consoleAutoscroll;
      transcriptAutoscrollBtn.setAttribute('aria-pressed', String(consoleAutoscroll));
      if (consoleAutoscroll && consoleTranscript) {
        consoleTranscript.scrollTop = consoleTranscript.scrollHeight;
      }
    });
  }

  if (transcriptClearBtn) {
    transcriptClearBtn.addEventListener('click', () => {
      const empty = document.createElement('div');
      empty.className = 'transcript-empty';
      empty.textContent = 'Transcript cleared';
      consoleTranscript.replaceChildren(empty);
    });
  }

  if (consoleFiltersContainer) {
    consoleFiltersContainer.addEventListener('change', (e) => {
      if (e.target.classList.contains('filter-checkbox')) {
        const filterType = e.target.dataset.filter;
        consoleFilters[filterType] = e.target.checked;
        // Re-render transcript with new filters
        pollConsoleSnapshot();
      }
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', applyState);
  } else {
    applyState();
  }
})();
"""


if __name__ == "__main__":
    raise SystemExit(main())
