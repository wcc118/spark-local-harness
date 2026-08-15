from __future__ import annotations

import argparse
import html
import json
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .diagnostics import Adjudication, LifecycleEvent, read_records
from .health import read_jsonl
from .read_model import RunView, build_run_views, summarize_views


@dataclass(frozen=True)
class DashboardSource:
    runs: Path
    adjudications: Path
    lifecycle: Path
    release: str = "0.1.0-alpha.1"

    def snapshot(self) -> dict[str, Any]:
        views = build_run_views(
            read_jsonl(self.runs),
            read_records(self.adjudications, Adjudication),
            read_records(self.lifecycle, LifecycleEvent),
        )
        return {
            "schema_version": 1,
            "release": self.release,
            "read_only": True,
            "summary": summarize_views(views),
            "runs": [view.as_dict() for view in views],
        }


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
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

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
        ("Frontier tokens", f"{summary['frontier_tokens']:,}"),
        ("Codex interventions", summary["codex_interventions"]),
    ]
    card_html = "".join(
        f'<section class="card"><span>{html.escape(str(label))}</span><strong>{html.escape(str(value))}</strong></section>'
        for label, value in cards
    )
    rows = "".join(_run_row(view) for view in views) or '<tr><td colspan="10">No diagnostic runs recorded.</td></tr>'
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
th,td{{padding:10px 11px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}} th{{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.05em}}
tr.contradiction{{background:#3a1e2a}} .passed{{color:var(--good)}} .failed,.interrupted,.timed-out,.cancelled{{color:var(--bad)}} .unadjudicated,.unknown{{color:var(--warn)}}
code{{color:#cbd5ff}} details{{max-width:380px;white-space:normal}} footer{{color:var(--muted);margin-top:16px}}
</style></head><body><main>
<h1>Harness diagnostic loop</h1><p class="subtitle">Release {html.escape(str(snapshot['release']))} · read-only · claims and observations remain separate</p>
<div class="cards">{card_html}</div>
<div class="table-wrap"><table><thead><tr><th>Run</th><th>Role / model</th><th>Task</th><th>Claim</th><th>Observed</th><th>Lifecycle</th><th>Delivery</th><th>Time</th><th>Tokens L/F</th><th>Evidence</th></tr></thead><tbody>{rows}</tbody></table></div>
<footer>Unknown and unadjudicated are first-class states. A contradiction means observed evidence disagrees with the worker claim.</footer>
</main></body></html>"""


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
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--release", default="0.1.0-alpha.1")
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("dashboard must bind to a loopback address")
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    source = DashboardSource(args.runs, args.adjudications, args.lifecycle, args.release)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(source))
    print(f"Read-only diagnostics: http://{args.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
