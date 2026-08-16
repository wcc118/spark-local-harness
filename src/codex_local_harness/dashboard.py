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
from urllib.parse import urlsplit

from .diagnostics import Adjudication, LifecycleEvent, PublicationFailure, read_records
from .frontier import FrontierStatus, latest_status
from .health import read_jsonl
from .read_model import RunView, build_run_views, summarize_views
from .workflow import WorkflowEvent, derive_operations


@dataclass(frozen=True)
class DashboardSource:
    runs: Path
    adjudications: Path
    lifecycle: Path
    workflow: Path | None = None
    frontier: Path | None = None
    publication_failures: Path | None = None
    release: str = "v0.1.1"
    mode: str = "live"
    now: datetime | None = None

    def _current_time(self) -> datetime:
        return self.now if self.now is not None else datetime.now(timezone.utc)

    def snapshot(self) -> dict[str, Any]:
        statuses: list[dict[str, Any]] = []
        runs = self._load(self.runs, "runs", read_jsonl, statuses)
        adjudications = self._load(self.adjudications, "adjudications", lambda path: read_records(path, Adjudication), statuses)
        lifecycle = self._load(self.lifecycle, "lifecycle", lambda path: read_records(path, LifecycleEvent), statuses)
        workflow = self._load(self.workflow, "workflow", lambda path: read_records(path, WorkflowEvent), statuses) if self.workflow else ()
        frontier_records = self._load(self.frontier, "frontier", lambda path: read_records(path, FrontierStatus), statuses) if self.frontier else ()
        publication_failures = self._load(
            self.publication_failures,
            "publication-failures",
            lambda path: read_records(path, PublicationFailure),
            statuses,
        ) if self.publication_failures else ()
        views = build_run_views(
            runs, adjudications, lifecycle,
        )
        frontier = latest_status(frontier_records)
        operations = derive_operations(workflow)
        current_operations = [operation for operation in operations if operation["current_state"] != "superseded"]
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
            route = urlsplit(self.path).path
            try:
                snapshot = source.snapshot()
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
    context = f"{codex['context_used']:,} / {codex['context_window']:,}" if codex["context_used"] is not None and codex["context_window"] is not None else "Unavailable"
    interactive_tokens = f"{codex['tokens_used']:,}" if codex["tokens_used"] is not None else "Unavailable"
    plan = f"{codex['plan_remaining_percent']:.1f}%" if codex["plan_remaining_percent"] is not None else "Unavailable"
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
.operation{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;margin:0 0 14px}} .operation-head{{display:flex;gap:16px;justify-content:space-between;flex-wrap:wrap}} .flow{{display:grid;grid-template-columns:repeat(10,minmax(110px,1fr));gap:6px;margin-top:12px;overflow:auto}} .stage{{position:relative;border:1px solid var(--line);border-radius:8px;padding:9px;min-width:110px}} .stage.pending{{color:var(--muted);border-color:#555b66;background:#22252d}} .stage.not-applicable{{color:#737b8c;border-color:#343946;background:transparent}} .stage.active{{border-color:var(--warn)}} .stage.active::after{{content:"";position:absolute;top:4px;left:4px;width:6px;height:6px;border-radius:50%;background:#fff5bd;box-shadow:0 0 3px 1px rgba(243,201,105,.5);pointer-events:none}} @media (prefers-reduced-motion:no-preference){{.stage.active::after{{animation:stage-orbit 4s linear infinite}}@keyframes stage-orbit{{0%,100%{{top:4px;left:4px}}25%{{top:4px;left:calc(100% - 10px)}}50%{{top:calc(100% - 10px);left:calc(100% - 10px)}}75%{{top:calc(100% - 10px);left:4px}}}}}}@media (prefers-reduced-motion:reduce){{.stage.active::after{{top:4px;right:4px;left:auto}}}} .stage.passed{{border-color:var(--good)}} .stage.failed,.stage.interrupted,.stage.blocked{{border-color:var(--bad)}} .stage-name{{display:block;font-size:11px;color:var(--muted)}}
.telemetry{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px;margin:0 0 22px}} .empty{{color:var(--muted)}} #connection{{color:var(--warn)}}
.source-warning{{border:1px solid var(--bad);background:#3a1e2a;border-radius:8px;padding:10px;margin-bottom:14px}}
th,td{{padding:10px 11px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}} th{{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em}}
tr.contradiction{{background:#3a1e2a}} .passed{{color:var(--good)}} .failed,.interrupted,.timed-out,.cancelled{{color:var(--bad)}} .unadjudicated,.unknown{{color:var(--warn)}}
code{{color:#cbd5ff}} details{{max-width:380px;white-space:normal}} footer{{color:var(--muted);margin-top:16px}}
</style></head><body><main>
<h1>Harness diagnostic loop</h1><p class="subtitle">Release {html.escape(str(snapshot['release']))} · <strong>{html.escape(str(snapshot['mode']).upper())}</strong> · read-only · <span id="connection">connecting</span></p>
{source_banner}
<div class="cards">{card_html}</div>
<div class="telemetry"><section class="card"><span>Interactive Codex tokens</span><strong>{html.escape(interactive_tokens)}</strong><small>{html.escape(str(codex['source']))}</small></section><section class="card"><span>Codex context</span><strong>{html.escape(context)}</strong><small>{html.escape(str(codex['source']))}</small></section><section class="card"><span>Plan remaining</span><strong>{html.escape(plan)}</strong><small>{html.escape(str(codex['source']))}</small></section><section class="card"><span>Plan refresh</span><strong>{html.escape(str(codex['plan_refresh_at'] or 'Unavailable'))}</strong><small>Never estimated</small></section><section class="card"><span>Source update age</span><strong data-age-seconds="{age_seconds if age_seconds is not None else ''}">{html.escape(newest_text)}</strong><small>{freshness['existing_files']} / {freshness['source_files']} sources present</small></section></div>
<h2>Current workflow</h2>{operations}
<h2>Run history</h2>
<div class="table-wrap"><table><thead><tr><th>Run</th><th>Role / model</th><th>Task</th><th>Claim</th><th>Observed</th><th>Lifecycle</th><th>Delivery</th><th>Time</th><th>Tokens L/F</th><th>Evidence</th></tr></thead><tbody>{rows}</tbody></table></div>
<footer>Unknown and unadjudicated are first-class states. A contradiction means observed evidence disagrees with the worker claim.</footer>
</main><script src="/dashboard.js"></script></body></html>"""


def _operation(operation: dict[str, Any]) -> str:
    stages = "".join(
        f'<div class="stage {html.escape(stage["state"])}"><span class="stage-name">{html.escape(stage["stage"])}</span><strong class="stage-state">{html.escape("not needed" if stage["state"] == "not-applicable" else stage["state"])}</strong></div>'
        for stage in operation["stages"]
    )
    stop = f' · stopped: {html.escape(str(operation["stop_reason"]))}' if operation["stop_reason"] else ""
    return f'<section class="operation"><div class="operation-head"><strong>{html.escape(operation["operation_id"])}</strong><span>issue {operation["issue"]} · {html.escape(operation["current_stage"])} / {html.escape(operation["current_state"])}{stop}</span></div><div class="flow">{stages}</div></section>'


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
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--release", default="v0.1.1")
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
  const events = new EventSource('/events');
  const connection = (text, color) => {
    const state = document.getElementById('connection');
    if (state) { state.textContent = text; state.style.color = color; }
  };
  const refresh = async () => {
    const response = await fetch('/', {cache: 'no-store'});
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
