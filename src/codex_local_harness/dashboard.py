from __future__ import annotations

import argparse
import html
import json
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
from .read_model import RunView, build_run_views, summarize_views
from .workflow import derive_operations, read_workflow_records


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
        views = build_run_views(
            runs, adjudications, lifecycle,
        )
        frontier = latest_status(frontier_records)
        operations = derive_operations(workflow)
        current_operations = [operation for operation in operations if operation["current_state"] != "superseded"]
        sessions = _derive_sessions(current_operations, self._current_time())
        selected = session_id if session_id and any(item["session_id"] == session_id for item in sessions) else None
        if selected is not None:
            current_operations = [item for item in current_operations if (item["session_id"] or "legacy") == selected]
            run_ids = {run_id for item in current_operations for run_id in item["run_ids"]}
            views = tuple(view for view in views if view.run_id in run_ids)
            runs = tuple(item for item in runs if item.run_id in run_ids)
        operation_usage = derive_usage(usage_snapshots)
        if selected is not None:
            operation_usage = tuple(item for item in operation_usage if item["session_id"] == selected)
        accounting = _accounting(runs, views, operation_usage, current_operations)
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
            "sessions": sessions,
            "selected_session": selected,
            "operation_usage": operation_usage,
            "accounting": accounting,
            "superseded_operations": sum(operation["current_state"] == "superseded" for operation in operations),
            "publication_failures": [failure.as_dict() for failure in publication_failures],
            "codex": frontier.as_dict() if frontier else FrontierStatus(
                "unavailable", "1970-01-01T00:00:00Z", "unavailable", note_code="official-interface-unavailable"
            ).as_dict(),
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


def make_handler(source: DashboardSource) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        server_version = "CodexHarnessDashboard/0.1"

        def do_GET(self) -> None:  # noqa: N802
            parsed_url = urlsplit(self.path)
            route = parsed_url.path
            requested_session = parse_qs(parsed_url.query).get("session", [None])[0]
            try:
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
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
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
    operations = "".join(_operation(item) for item in snapshot["operations"]) or '<p class="empty">No workflow events recorded.</p>'
    codex = snapshot["codex"]
    freshness = snapshot["freshness"]
    age_seconds = freshness["newest_age_seconds"]
    newest_text = _format_age(age_seconds) if age_seconds is not None else "No data"
    source_problems = [source for source in snapshot["sources"] if source["state"] != "ok"]
    source_banner = "" if not source_problems else '<section class="source-warning"><strong>Source warning:</strong> ' + ", ".join(html.escape(f"{source['name']}={source['state']}") for source in source_problems) + "</section>"
    if snapshot["publication_failures"]:
        source_banner += f'<section class="source-warning"><strong>Telemetry publication failures:</strong> {len(snapshot["publication_failures"])}</section>'
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
        f'<option value="{html.escape(item["session_id"])}"{" selected" if snapshot["selected_session"] == item["session_id"] else ""}>{html.escape(item["session_id"])} · {html.escape(item["status"])}</option>'
        for item in snapshot["sessions"]
    ]
    session_picker = f'<form class="session-picker" method="get"><label>Session <select name="session" onchange="this.form.submit()">{"".join(session_options)}</select></label></form>'
    accounting_html = _accounting_html(snapshot["accounting"])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Codex agent harness diagnostics</title>
<style>
:root{{--bg:#0b1020;--panel:#151c30;--ink:#eef2ff;--muted:#9aa6c3;--line:#2a3552;--good:#59d499;--warn:#f3c969;--bad:#ff7885}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 ui-sans-serif,system-ui,sans-serif}}
main{{max-width:1500px;margin:auto;padding:28px}} h1{{margin:0;font-size:25px}} .subtitle{{color:var(--muted);margin:5px 0 22px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:10px;margin-bottom:22px}} .card{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}}
.card span{{display:block;color:var(--muted);font-size:12px}} .card strong{{display:block;font-size:21px;margin-top:3px}}
.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:10px}} table{{width:100%;border-collapse:collapse;background:var(--panel)}}
.operation{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;margin:0 0 14px;overflow:hidden}} .operation-head{{display:flex;gap:16px;justify-content:space-between;flex-wrap:wrap}} .flow{{display:grid;grid-template-columns:repeat(8,minmax(120px,1fr));gap:8px;margin-top:12px;overflow:auto;padding:2px}} .milestone{{position:relative;border:1px solid var(--line);border-radius:8px;padding:9px;min-width:120px}} .milestone.pending{{color:#626b7c;border-color:#343946;background:#181b22}} .milestone.not-applicable{{color:#737b8c;border-color:#343946;background:transparent}} .milestone.active{{border-color:var(--warn)}} .milestone.active::after{{content:"";position:absolute;top:4px;left:4px;width:6px;height:6px;border-radius:50%;background:#fff5bd;box-shadow:0 0 3px 1px rgba(243,201,105,.5);pointer-events:none}} @media (prefers-reduced-motion:no-preference){{.milestone.active::after{{animation:milestone-orbit 4s linear infinite}}@keyframes milestone-orbit{{0%,100%{{top:4px;left:4px}}25%{{top:4px;left:calc(100% - 10px)}}50%{{top:calc(100% - 10px);left:calc(100% - 10px)}}75%{{top:calc(100% - 10px);left:4px}}}}}}@media (prefers-reduced-motion:reduce){{.milestone.active::after{{top:4px;right:4px;left:auto}}}} .milestone.passed{{border-color:var(--good)}} .milestone.failed,.milestone.interrupted,.milestone.blocked,.milestone.conflicting{{border-color:var(--bad)}} .milestone-name{{display:block;font-size:11px;color:var(--muted)}} .milestone-activities{{margin-top:6px}} .milestone-activity{{display:inline-block;background:var(--line);color:var(--ink);border-radius:4px;padding:2px 6px;font-size:9px;margin-right:4px;margin-bottom:4px}} .unassigned-activities{{margin:9px 2px 0;padding:7px 10px;border:1px dashed var(--warn);border-radius:5px;color:var(--warn)}} .correction-loop{{margin:9px 2px 0;padding:7px 10px;border-left:3px solid var(--warn);background:#272417;color:var(--warn);border-radius:4px}} .workflow-diagnostic{{display:inline-block;margin-top:8px;padding:3px 7px;border:1px solid var(--bad);color:var(--bad);border-radius:5px;font-size:11px}} @media (max-width:760px){{.flow{{grid-template-columns:1fr;overflow:visible}}.milestone{{min-width:0}}}}
.telemetry{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px;margin:0 0 22px}} .empty{{color:var(--muted)}} #connection{{color:var(--warn)}}
.session-picker{{margin:0 0 14px}} select{{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:7px;padding:7px}}
.source-warning{{border:1px solid var(--bad);background:#3a1e2a;border-radius:8px;padding:10px;margin-bottom:14px}}
th,td{{padding:10px 11px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}} th{{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em}}
tr.contradiction{{background:#3a1e2a}} .passed{{color:var(--good)}} .failed,.interrupted,.timed-out,.cancelled{{color:var(--bad)}} .unadjudicated,.unknown{{color:var(--warn)}}
code{{color:#cbd5ff}} details{{max-width:380px;white-space:normal}} footer{{color:var(--muted);margin-top:16px}}
</style></head><body><main>
<h1>Harness diagnostic loop</h1><p class="subtitle">Release {html.escape(str(snapshot['release']))} · <strong>{html.escape(str(snapshot['mode']).upper())}</strong> · read-only · <span id="connection">connecting</span></p>
{source_banner}
<div class="cards">{card_html}</div>
<div class="telemetry">{telemetry_html}</div>
{session_picker}
<h2>Division of labor</h2>{accounting_html}
<h2>Current workflow</h2>{operations}
<h2>Run history</h2>
<div class="table-wrap"><table><thead><tr><th>Run</th><th>Role / model</th><th>Task</th><th>Claim</th><th>Observed</th><th>Lifecycle</th><th>Delivery</th><th>Time</th><th>Tokens L/F</th><th>Evidence</th></tr></thead><tbody>{rows}</tbody></table></div>
<footer>Unknown and unadjudicated are first-class states. A contradiction means observed evidence disagrees with the worker claim.</footer>
</main><script src="/dashboard.js"></script></body></html>"""


def _operation(operation: dict[str, Any]) -> str:
    milestones = "".join(
        f'<div class="milestone {html.escape(m["state"])}"><span class="milestone-name">{html.escape(m["label"])}</span><strong class="milestone-state">{html.escape("not needed" if m["state"] == "not-applicable" else m["state"])}</strong>{_milestone_activities_html(m["activities"])}</div>'
        for m in operation["milestones"]
    )
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
    return f'<section class="operation"><div class="operation-head"><strong>{title}</strong><span>{issue_text} · session {session} · {html.escape(operation["current_milestone"])} / {html.escape(operation["current_state"])}{stop}</span></div>{diagnostics}<div class="flow" aria-label="Delivery milestones">{milestones}</div>{unassigned_html}{loop}</section>'


def _milestone_activities_html(activities: list[dict[str, Any]]) -> str:
    if not activities:
        return ""
    activity_html = " ".join(
        f'<span class="milestone-activity {html.escape(item["state"])}">{html.escape(item["activity_type"])} · {html.escape("not needed" if item["state"] == "not-applicable" else item["state"])}</span>'
        for item in activities
    )
    return f'<div class="milestone-activities">{activity_html}</div>'


def _derive_sessions(operations: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
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
        sessions.append({"session_id": session_id, "status": status, "updated_at": newest["updated_at"], "operations": len(items)})
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


def _accounting_html(accounting: dict[str, Any]) -> str:
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
        f'local {accounting["local_uncached"]:,} / Codex {accounting["codex_uncached"]:,}'
        if accounting["local_uncached"] is not None and accounting["codex_uncached"] is not None
        else "Not fully reported"
    )
    return (
        '<div class="cards">'
        f'<section class="card"><span>Processed context · local share</span><strong>{local_processed_share}</strong><small>local {accounting["local_input"]:,} / Codex {accounting["codex_input"] if accounting["codex_input"] is not None else "not reported"}</small></section>'
        f'<section class="card"><span>Generated + reasoning · local share</span><strong>{local_generated_share}</strong><small>local {accounting["local_generated"]:,} / Codex {accounting["codex_generated"] if accounting["codex_generated"] is not None else "not reported"}</small></section>'
        f'<section class="card"><span>Cached input</span><strong>local {cached}</strong><small>Codex {codex_cached} · uncached {uncached}</small></section>'
        f'<section class="card"><span>Operational contribution</span><strong>{accounting["worker_runs"]} local runs</strong><small>{accounting["codex_interventions"]} Codex interventions · {accounting["corrections"]} corrections · {accounting["takeovers"]} takeovers</small></section>'
        '</div>'
    )


def _run_row(view: dict[str, Any]) -> str:
    css = "contradiction" if view["contradiction"] else ""
    outcome = html.escape(str(view["effective_outcome"]))
    evidence = "<br>".join(html.escape(str(item)) for item in view["evidence_refs"]) or "—"
    role_model = f"{html.escape(str(view['role'] or 'unknown'))}<br><code>{html.escape(str(view['model'] or 'unknown'))}</code>"
    return (
        f'<tr class="{css}"><td><code>{html.escape(str(view["run_id"]))}</code><br>issue {view["issue"] or "—"}</td>'
        f'<td>{role_model}</td><td>{html.escape(str(view["task_class"] or "unknown"))}</td>'
        f'<td>{html.escape(str(view["claimed_status"] or "none"))}</td><td class="{outcome}">{outcome}</td>'
        f'<td>{html.escape(str(view["lifecycle_state"]))}</td><td>{html.escape(str(view["delivery_ready"]))}</td>'
        f'<td>{view["wall_seconds"]:.1f}s</td><td>{view["local_tokens"]:,} / {view["frontier_tokens"]:,}</td>'
        f'<td><details><summary>{len(view["evidence_refs"])} refs</summary>{evidence}</details></td></tr>'
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
  const events = new EventSource('/events' + window.location.search);
  const connection = (text, color) => {
    const state = document.getElementById('connection');
    if (state) { state.textContent = text; state.style.color = color; }
  };
  const refresh = async () => {
    const response = await fetch('/' + window.location.search, {cache: 'no-store'});
    if (!response.ok) { throw new Error('dashboard refresh failed'); }
    const parsed = new DOMParser().parseFromString(await response.text(), 'text/html');
    const current = document.querySelector('main');
    const replacement = parsed.querySelector('main');
    if (current && replacement) { current.replaceWith(replacement); }
    connection('live', 'var(--good)');
  };
  events.addEventListener('open', () => connection('live', 'var(--good)'));
  events.addEventListener('snapshot', () => {
    if (initial) { initial = false; } else { refresh().catch(() => connection('stale', 'var(--bad)')); }
  });
  events.addEventListener('error', () => connection('disconnected', 'var(--bad)'));
})();
"""


if __name__ == "__main__":
    raise SystemExit(main())
