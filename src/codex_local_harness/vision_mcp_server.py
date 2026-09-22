"""MCP server exposing the vision-gate capability as a callable tool (issue #205).

Runs as a persistent local service (default: loopback HTTP, streamable-http
transport) that the coder and reviewer agents reach as a "remote" MCP server
over the network their worker containers already have (--network host), the
same way they already reach vLLM and DeepSeek. It is NOT run inside a worker
container itself, so it does not require the worker image to carry the `mcp`
package or any harness source -- only this process's own environment needs
those, and it is started once, out of band, like the dashboard or DeepSeek.

Tool: `review_screenshot(path)`. It never raises to the caller: every failure
mode (lane unavailable, bad path, budget exhausted, model/schema failure)
comes back as a normal tool result with `available: false` and a short
`detail` string, so a coder or reviewer agent can read the result and decide
what to do without a crashed tool call derailing its turn.

Built and measured for dashboard/UI-style screenshots (see qualification/
vision/README.md): reliable on defects with a numeric or textual anchor to
cross-check against, less reliable on pure layout-only issues with nothing to
anchor on. General-purpose image understanding beyond that is unvalidated by
design -- the owner's explicit direction was to ship this now and learn its
real boundaries through use rather than hold it back for more upfront testing
(issue #205).

Security note: because this process runs outside the worker sandbox, `path`
is restricted to a configured allowlist of roots (default: the harness's own
isolated-checkout/worker-home/reviewer-scratch directories and its own vision
fixtures) so a compromised or confused calling agent cannot use this tool as a
side channel to read arbitrary files on the host the container sandbox would
otherwise have kept it from reaching.
"""
from __future__ import annotations

import argparse
import os
import threading
import time
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from .lane import GpuLease, check_lane
from .vision_judge import score_one_call

DEFAULT_MODEL = "qwen/qwen3.8-27b"
DEFAULT_MIN_CONTEXT = 65536
DEFAULT_TIMEOUT = 180.0
DEFAULT_MAX_TOKENS = 3200
DEFAULT_MAX_CALLS = 5
DEFAULT_LEASE_TIMEOUT = 30.0
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

TOOL_DESCRIPTION = (
    "Review a PNG screenshot of a dashboard or UI for internal inconsistencies "
    "(a chart that contradicts its own legend or axis, a total that does not "
    "match the rows it sums, a value that does not match a table next to it, "
    "misaligned content, placeholder text, implausible dates) and transcribe "
    "any clearly legible labelled numeric values. Reliable when a finding has "
    "a number or word elsewhere on the page to cross-check against; less "
    "reliable on a pure layout defect with nothing to anchor on. Built for "
    "dashboard/UI-style screenshots; general image understanding beyond that "
    "is unvalidated. Bounded to a small number of calls per server process."
)


def _default_allowed_roots() -> list[Path]:
    home = Path.home()
    return [
        home / ".local/share/opencode-orchestration/isolated-checkouts",
        home / ".local/share/opencode-orchestration/worker-homes",
        home / ".local/share/opencode-orchestration/reviewer-scratch",
        home / "projects/codex-local-harness/qualification/vision/fixtures",
    ]


class VisionReviewService:
    """Holds the configuration and per-process call budget for the tool."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str = DEFAULT_MODEL,
        min_context: int = DEFAULT_MIN_CONTEXT,
        timeout: float = DEFAULT_TIMEOUT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_calls: int = DEFAULT_MAX_CALLS,
        lease_timeout: float = DEFAULT_LEASE_TIMEOUT,
        allowed_roots: list[Path] | None = None,
    ) -> None:
        self.base_url = base_url
        self.model = model
        self.min_context = min_context
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.max_calls = max_calls
        self.lease_timeout = lease_timeout
        self.allowed_roots = [r.resolve() for r in (allowed_roots or _default_allowed_roots())]
        self._lock = threading.Lock()
        self._calls_made = 0

    def _reserve_call(self) -> str | None:
        """Return None if a call may proceed, else a reason it may not."""
        with self._lock:
            if self._calls_made >= self.max_calls:
                return f"per-process call budget exhausted ({self.max_calls} calls)"
            self._calls_made += 1
            return None

    def validate_path(self, path_str: str) -> tuple[Path | None, str | None]:
        """Return (resolved_path, None) if path is allowed and a real PNG, else (None, reason)."""
        if not path_str or not isinstance(path_str, str):
            return None, "path must be a non-empty string"
        try:
            resolved = Path(path_str).resolve(strict=True)
        except OSError as exc:
            return None, f"path does not exist or cannot be resolved: {exc}"
        if not resolved.is_file():
            return None, "path is not a regular file"
        if not any(resolved == root or root in resolved.parents for root in self.allowed_roots):
            return None, "path is outside the allowed roots for this tool"
        try:
            with resolved.open("rb") as fh:
                head = fh.read(8)
        except OSError as exc:
            return None, f"could not read path: {exc}"
        if head != PNG_MAGIC:
            return None, "path is not a PNG file"
        return resolved, None

    def review(self, path_str: str) -> dict:
        resolved, reason = self.validate_path(path_str)
        if reason is not None:
            return {"available": False, "detail": reason, "findings": [], "numeric_readings": []}

        budget_reason = self._reserve_call()
        if budget_reason is not None:
            return {"available": False, "detail": budget_reason, "findings": [], "numeric_readings": []}

        status = check_lane(self.base_url, self.model, min_context=self.min_context, timeout=10.0)
        if status.status != "ok":
            return {
                "available": False,
                "detail": f"vision lane not ready ({status.status}): {status.detail}",
                "findings": [],
                "numeric_readings": [],
            }

        lease = GpuLease("3090", purpose="vision-mcp-review")
        if not lease.acquire(timeout=self.lease_timeout):
            return {
                "available": False,
                "detail": f"could not acquire the 3090 lease within {self.lease_timeout}s",
                "findings": [],
                "numeric_readings": [],
            }
        try:
            png_bytes = resolved.read_bytes()
            result = score_one_call(self.base_url, self.model, png_bytes, self.timeout, self.max_tokens)
        finally:
            lease.release()

        if result["outcome"] != "ok":
            return {
                "available": False,
                "detail": f"vision model call did not produce a valid reply: {result['outcome']}",
                "findings": [],
                "numeric_readings": [],
            }
        parsed = result["parsed"]
        return {
            "available": True,
            "detail": "ok",
            "findings": parsed.get("findings", []),
            "numeric_readings": parsed.get("numeric_readings", []),
        }


def build_server(service: VisionReviewService) -> MCPServer:
    server = MCPServer(name="vision-review", version="1.0.0")

    @server.tool(description=TOOL_DESCRIPTION)
    def review_screenshot(path: str) -> dict:
        return service.review(path)

    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=None, help="Defaults to $LMSTUDIO_BASE_URL")
    parser.add_argument("--model", default=os.environ.get("CODEX_LOCAL_VISION_MODEL", DEFAULT_MODEL))
    parser.add_argument("--min-context", type=int, default=int(os.environ.get("CODEX_LOCAL_VISION_MIN_CONTEXT", DEFAULT_MIN_CONTEXT)))
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("CODEX_LOCAL_VISION_TIMEOUT", DEFAULT_TIMEOUT)))
    parser.add_argument("--max-tokens", type=int, default=int(os.environ.get("CODEX_LOCAL_VISION_MAX_TOKENS", DEFAULT_MAX_TOKENS)))
    parser.add_argument("--max-calls", type=int, default=int(os.environ.get("CODEX_LOCAL_VISION_MAX_CALLS", DEFAULT_MAX_CALLS)))
    parser.add_argument("--lease-timeout", type=float, default=float(os.environ.get("CODEX_LOCAL_VISION_LEASE_TIMEOUT", DEFAULT_LEASE_TIMEOUT)))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790)
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="streamable-http")
    parser.add_argument(
        "--allowed-root", action="append", dest="allowed_roots", default=None,
        help="Restrict `path` to this root; repeatable. Defaults to the harness's own "
             "isolated-checkout/worker-home/reviewer-scratch/vision-fixtures directories.",
    )
    args = parser.parse_args(argv)

    base_url = args.base_url or os.environ.get("LMSTUDIO_BASE_URL")
    if not base_url:
        parser.error("--base-url or LMSTUDIO_BASE_URL is required")

    allowed_roots = [Path(r) for r in args.allowed_roots] if args.allowed_roots else None
    service = VisionReviewService(
        base_url=base_url, model=args.model, min_context=args.min_context, timeout=args.timeout,
        max_tokens=args.max_tokens, max_calls=args.max_calls, lease_timeout=args.lease_timeout,
        allowed_roots=allowed_roots,
    )
    server = build_server(service)

    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        print(f"vision-review MCP server starting on http://{args.host}:{args.port} at {started} (model={args.model})")
        server.run(transport="streamable-http", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
