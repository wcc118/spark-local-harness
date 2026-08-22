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

from .diagnostics import Adjudication, LifecycleEvent, PublicationFailure, read_records
from .frontier import FrontierStatus, latest_status
from .health import read_jsonl
from .operation_usage import OperationUsageSnapshot, derive_usage
from .plan_event import derive_plans, read_plan_records
from .read_model import RunView, build_run_views, summarize_views
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

    def _current_time(self) -> datetime:
        return self.now if self.now is not None else datetime.now(timezone.utc)

    def snapshot(self, session_id: str | None = None) -> dict[str, Any]:
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
        ) if path is not None)

    def fingerprint(self) -> tuple[tuple[str, int, int], ...]:
        result = []
        for path in self.paths():
            try:
                stat = path.stat()
                result.append((str(path), stat.st_mtime_ns, stat.st_size))
            except FileNotFoundError:
                result.append((str(path), 0, 0))
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

        item_html = (
            f'<li class="attention-item {item_sev_class}">'
            f'<span class="attention-severity">{html.escape(item_severity.upper())}</span>'
            f'<span class="attention-subject">{subject}</span>'
            f'<span class="attention-reason">{html.escape(item.get("reason", ""))}</span>'
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


FAVICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
<rect width="64" height="64" rx="14" fill="#0b1020"/>
<circle cx="32" cy="32" r="21" fill="none" stroke="#2a3552" stroke-width="4"/>
<path d="M43 20a17 17 0 1 0 0 24" fill="none" stroke="#59d499" stroke-width="7" stroke-linecap="round"/>
<circle cx="48" cy="16" r="5" fill="#f3c969"/>
</svg>"""


def make_handler(source: DashboardSource) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "CodexHarnessDashboard/0.1"

        def do_GET(self) -> None:  # noqa: N802
            parsed_url = urlsplit(self.path)
            route = parsed_url.path
            requested_session = parse_qs(parsed_url.query).get("session", [None])[0]
            try:
                if route == "/favicon.svg":
                    self._send(HTTPStatus.OK, "image/svg+xml", FAVICON_SVG.encode())
                    return
                snapshot = source.snapshot(requested_session)
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
                    self._send(HTTPStatus.OK, "application/json", b'{"status":"ok","read_only":true}\n')
                else:
                    self._send(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"Not found\n")
            except (OSError, ValueError) as exc:
                body = json.dumps({"error": type(exc).__name__, "read_only": True}).encode()
                self._send(HTTPStatus.INTERNAL_SERVER_ERROR, "application/json", body)

        def do_POST(self) -> None:  # noqa: N802
            self._method_not_allowed()

        do_PUT = do_POST
        do_PATCH = do_POST
        do_DELETE = do_POST

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
.attention-item{{border:1px solid var(--line);border-radius:6px;padding:8px;display:grid;grid-template-columns:60px 1fr auto;gap:8px;align-items:center}}
.attention-item.critical{{border-color:var(--bad)}} .attention-item.warn{{border-color:var(--warn)}}
.attention-severity{{display:flex;justify-content:center;align-items:center;padding:2px 6px;border-radius:4px;font-size:10px;font-weight:700;text-transform:uppercase;background:#2a3552;color:var(--ink)}}
.attention-severity.CRITICAL{{background:var(--bad);color:#000}} .attention-severity.DEGRADED{{background:var(--warn);color:#000}}
.attention-subject{{font-weight:600;color:var(--ink);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.attention-reason{{color:var(--muted);font-size:12px;white-space:nowrap}}
.omitted-count{{color:var(--muted);font-size:11px}}
</style>
</head><body><main>
<h1>Harness diagnostic loop</h1><p class="subtitle">Release {html.escape(str(snapshot['release']))} · <strong>{html.escape(str(snapshot['mode']).upper())}</strong> · read-only · <span id="connection">connecting</span> · <span class="freshness {freshness_severity}">{html.escape(newest_text)}</span></p>
{source_banner}
{verdict_html}
<div class="cards">{card_html}</div>
<div class="telemetry">{telemetry_html}</div>
{session_picker}
<h2>Division of labor</h2>{accounting_html}
<h2>Current operations</h2>{operations}
<h2>Run history</h2>
{triage_toolbar}
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
    return (
        '<section class="running-plan">'
        f'<div class="plan-head"><h3>Running plan</h3><span>{plan["completed_steps"]} / {plan["total_steps"]} · revision {plan["revision"]}</span></div>'
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
    return f'<section class="operation"><h3>Current workflow</h3><div class="operation-head"><strong>{title}</strong><span>{issue_text} · session {session} · {html.escape(operation["current_milestone"])} / {html.escape(operation["current_state"])}{stop}</span></div>{diagnostics}{flow}{unassigned_html}{loop}</section>'


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
    return (
        f'<tr class="{css}" data-triage="{triage}" data-search="{search}"><td><code>{run_id}</code><br>issue {view["issue"] or "—"}</td>'
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
    )
    server = ThreadingHTTPServer((args.host, args.port), make_handler(source))
    print(f"Read-only diagnostics: http://{args.host}:{server.server_port}", flush=True)
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
  };

  document.addEventListener('click', (event) => {
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

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', applyState);
  } else {
    applyState();
  }
})();
"""


if __name__ == "__main__":
    raise SystemExit(main())
