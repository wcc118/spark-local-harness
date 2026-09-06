from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol, TextIO

from .context_checkpoint import (
    ContextCheckpointEvent,
    checkpoint_diagnostics,
    checkpoint_packet,
)
from .context_controller import (
    CompactionLifecycleEvent,
    CompactionPolicy,
    CompactionReadiness,
    CompactionRejected,
    CompactionTracker,
    append_compaction_record,
    authorize_compaction,
)


NotificationHandler = Callable[[dict[str, Any]], None]


class AppServerError(RuntimeError):
    pass


class AppServerTransport(Protocol):
    def request(
        self, method: str, params: dict[str, Any], *, timeout: float,
        on_notification: NotificationHandler | None = None,
    ) -> dict[str, Any]: ...

    def receive(self, *, timeout: float) -> dict[str, Any]: ...


class StdioAppServer:
    """Private, process-owned Codex app-server JSONL connection.

    This transport never discovers or attaches to sockets. Stderr is discarded
    because app-server logs can include user or tool content; callers retain only
    normalized records emitted by ``ManagedCodexController``.
    """

    def __init__(self, command: tuple[str, ...] = ("codex", "app-server")) -> None:
        self.command = command
        self._process: subprocess.Popen[str] | None = None
        self._messages: queue.Queue[dict[str, Any] | BaseException] = queue.Queue()
        self._next_id = 1
        self._write_lock = threading.Lock()
        self._reader: threading.Thread | None = None

    def start(self, *, timeout: float = 10.0) -> None:
        if self._process is not None:
            raise AppServerError("app-server is already started")
        try:
            process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except OSError as exc:
            raise AppServerError("unable to start codex app-server") from exc
        if process.stdin is None or process.stdout is None:
            process.terminate()
            raise AppServerError("app-server stdio is unavailable")
        self._process = process
        self._reader = threading.Thread(
            target=self._read_messages, args=(process.stdout,), daemon=True,
        )
        self._reader.start()
        self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "codex-local-harness",
                    "title": "Codex Local Harness",
                    "version": "1",
                },
                "capabilities": {"experimentalApi": False},
            },
            timeout=timeout,
        )
        self._write({"method": "initialized"})

    def close(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def __enter__(self) -> StdioAppServer:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def request(
        self, method: str, params: dict[str, Any], *, timeout: float,
        on_notification: NotificationHandler | None = None,
    ) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        self._write({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        deferred: list[dict[str, Any]] = []
        try:
            while True:
                message = self._take(max(0.0, deadline - time.monotonic()))
                if "id" not in message:
                    if on_notification is not None:
                        on_notification(message)
                    else:
                        deferred.append(message)
                    continue
                if message.get("id") != request_id:
                    raise AppServerError("app-server returned an unexpected response id")
                if "error" in message:
                    raise AppServerError("app-server request failed")
                result = message.get("result")
                if not isinstance(result, dict):
                    raise AppServerError("app-server returned a malformed result")
                return result
        finally:
            for notification in deferred:
                self._messages.put(notification)

    def receive(self, *, timeout: float) -> dict[str, Any]:
        message = self._take(timeout)
        if "id" in message:
            raise AppServerError("unexpected app-server response without a request")
        return message

    def _read_messages(self, stdout: TextIO) -> None:
        try:
            for line in stdout:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise AppServerError("app-server emitted a non-object message")
                self._messages.put(value)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._messages.put(AppServerError("app-server emitted malformed JSON"))
        finally:
            self._messages.put(AppServerError("app-server connection closed"))

    def _take(self, timeout: float) -> dict[str, Any]:
        if timeout <= 0:
            raise AppServerError("app-server response timed out")
        try:
            value = self._messages.get(timeout=timeout)
        except queue.Empty as exc:
            raise AppServerError("app-server response timed out") from exc
        if isinstance(value, BaseException):
            raise value
        return value

    def _write(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.poll() is not None:
            raise AppServerError("app-server is not running")
        encoded = json.dumps(message, separators=(",", ":"), ensure_ascii=True)
        with self._write_lock:
            try:
                process.stdin.write(encoded + "\n")
                process.stdin.flush()
            except OSError as exc:
                raise AppServerError("app-server write failed") from exc


@dataclass(frozen=True)
class CurrentCheckpointState:
    repository: str
    issue: int
    pull_request: int | None
    head_sha: str
    plan_event_id: str
    plan_revision: int
    milestone_completed: bool
    terminal_milestone: bool
    remaining_work: bool
    turns_since_compaction: int


class ManagedCodexController:
    """Own one issue-scoped app-server thread and its compaction lifecycle."""

    def __init__(
        self, transport: AppServerTransport, *, run_id: str, operation_id: str,
        repository: str, issue: int, records: Path,
        policy: CompactionPolicy = CompactionPolicy(),
    ) -> None:
        self.transport = transport
        self.run_id = run_id
        self.operation_id = operation_id
        self.repository = repository
        self.issue = issue
        self.records = records
        self.policy = policy
        self.thread_id: str | None = None
        self.latest_completed_turn_id: str | None = None
        self.turn_active = False
        self.tool_active = False
        self.last_input_tokens: int | None = None
        self.context_window: int | None = None
        self.tracker: CompactionTracker | None = None

    def start_fresh_issue_thread(
        self, checkpoint: ContextCheckpointEvent, *, cwd: Path,
        timeout: float = 10.0,
    ) -> tuple[str, str]:
        if self.thread_id is not None:
            raise AppServerError("controller already owns a thread")
        if not cwd.is_absolute():
            raise AppServerError("managed thread cwd must be absolute")
        if checkpoint.repository != self.repository or checkpoint.issue != self.issue:
            raise CompactionRejected("checkpoint-identity-mismatch")
        packet, _ = checkpoint_packet(checkpoint)
        response = self.transport.request(
            "thread/start", {"cwd": str(cwd), "ephemeral": False}, timeout=timeout,
        )
        thread = response.get("thread")
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(thread_id, str) or not thread_id:
            raise AppServerError("thread/start returned no thread identity")
        self.thread_id = thread_id
        self.tracker = CompactionTracker(
            run_id=self.run_id, session_id=thread_id,
            repository=self.repository, issue=self.issue,
        )
        turn_response = self.transport.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": packet}],
            },
            timeout=timeout,
            on_notification=self.observe,
        )
        turn = turn_response.get("turn")
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        if not isinstance(turn_id, str) or not turn_id:
            raise AppServerError("turn/start returned no turn identity")
        self.turn_active = True
        return thread_id, turn_id

    def observe(self, notification: dict[str, Any]) -> None:
        params = notification.get("params")
        if not isinstance(params, dict) or params.get("threadId") != self.thread_id:
            return
        method = notification.get("method")
        if method == "turn/started":
            self.turn_active = True
        elif method == "turn/completed":
            turn = params.get("turn")
            if isinstance(turn, dict) and turn.get("status") == "completed":
                turn_id = turn.get("id")
                if isinstance(turn_id, str):
                    self.latest_completed_turn_id = turn_id
            self.turn_active = False
            self.tool_active = False
        elif method == "item/started":
            item = params.get("item")
            if isinstance(item, dict) and item.get("type") != "contextCompaction":
                self.tool_active = True
        elif method == "item/completed":
            item = params.get("item")
            if isinstance(item, dict) and item.get("type") != "contextCompaction":
                self.tool_active = False
        elif method == "thread/tokenUsage/updated":
            usage = params.get("tokenUsage")
            last = usage.get("last") if isinstance(usage, dict) else None
            input_tokens = last.get("inputTokens") if isinstance(last, dict) else None
            window = usage.get("modelContextWindow") if isinstance(usage, dict) else None
            if isinstance(input_tokens, int) and not isinstance(input_tokens, bool) and input_tokens >= 0:
                self.last_input_tokens = input_tokens
            if isinstance(window, int) and not isinstance(window, bool) and window > 0:
                self.context_window = window
        if self.tracker is not None:
            record = self.tracker.observe(notification, observed_at=_now())
            if record is not None:
                append_compaction_record(self.records, record)

    def request_compaction(
        self, checkpoint: ContextCheckpointEvent, current: CurrentCheckpointState,
        *, timeout: float = 30.0,
    ) -> CompactionLifecycleEvent:
        if self.thread_id is None or self.tracker is None:
            raise CompactionRejected("session-not-controller-owned")
        diagnostics = checkpoint_diagnostics(
            checkpoint,
            repository=current.repository,
            issue=current.issue,
            pull_request=current.pull_request,
            head_sha=current.head_sha,
            plan_event_id=current.plan_event_id,
            plan_revision=current.plan_revision,
            latest_completed_turn_id=self.latest_completed_turn_id or "unavailable",
        )
        if checkpoint.session_id != self.thread_id:
            diagnostics += ("stale-session",)
        readiness = CompactionReadiness(
            controller_owned=True,
            thread_idle=not self.turn_active,
            tool_active=self.tool_active,
            checkpoint_current=not diagnostics,
            milestone_completed=current.milestone_completed,
            terminal_milestone=current.terminal_milestone,
            remaining_work=current.remaining_work,
            last_input_tokens=self.last_input_tokens,
            context_window=self.context_window,
            turns_since_compaction=current.turns_since_compaction,
        )
        if diagnostics:
            raise CompactionRejected(",".join(diagnostics))
        authorize_compaction(readiness, self.policy)
        compaction_id = f"compact:{uuid.uuid4().hex}"
        requested = self.tracker.requested(
            compaction_id=compaction_id,
            observed_at=_now(),
            before_input_tokens=self.last_input_tokens,
            context_window=self.context_window,
        )
        append_compaction_record(self.records, requested)
        try:
            self.transport.request(
                "thread/compact/start", {"threadId": self.thread_id},
                timeout=min(timeout, 10.0), on_notification=self.observe,
            )
            deadline = time.monotonic() + timeout
            while self.tracker.state != "failed" and not (
                self.tracker.state == "completed"
                and not self.tracker.awaiting_turn_completion
            ):
                self.observe(self.transport.receive(timeout=max(0.0, deadline - time.monotonic())))
        except (AppServerError, ValueError) as exc:
            error = exc if isinstance(exc, AppServerError) else AppServerError(
                "app-server emitted an invalid compaction lifecycle"
            )
            failed = self.tracker.failed(
                observed_at=_now(), error_class=_error_class(error),
            )
            append_compaction_record(self.records, failed)
            raise error from exc
        records = [
            record for record in read_compaction_records(self.records)
            if record.compaction_id == compaction_id
        ]
        if not records:
            raise AppServerError("compaction completed without a lifecycle record")
        latest = max(records, key=lambda item: (item.observed_at, item.record_id))
        if latest.state == "failed":
            raise AppServerError("compaction lifecycle failed")
        return latest


def read_compaction_records(path: Path) -> tuple[CompactionLifecycleEvent, ...]:
    # Local import avoids broadening the public surface of the controller module.
    from .context_controller import read_compaction_records as read_records

    return read_records(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _error_class(exc: AppServerError) -> str:
    if "timed out" in str(exc):
        return "app-server-timeout"
    if "invalid compaction lifecycle" in str(exc):
        return "app-server-protocol"
    return "app-server-transport"
