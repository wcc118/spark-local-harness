from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from typing import Any

from .diagnostics import _safe_atom, _timestamp
from .workflow import REPOSITORY_PATTERN


COMPACTION_SCHEMA_VERSION = 1
COMPACTION_STATES = {
    "advisory", "requested", "started", "completed", "failed",
    "post-context-observed",
}
COMPACTION_TRIGGERS = {"automatic", "manual", "advisory"}
SURFACES = {"primary-codex", "local-opencode"}
SUPPORTED_OPENCODE_VERSION = "1.18.21"
OPENCODE_COMPACTION_EVENTS = frozenset({
    "session.compacted",
    "session.next.compaction.started",
    "session.next.compaction.delta",
    "session.next.compaction.ended",
})


def verify_opencode_compaction_contract(
    *, version: str, compaction: dict[str, Any],
) -> None:
    if version != SUPPORTED_OPENCODE_VERSION:
        raise ValueError(
            f"unsupported OpenCode compaction contract version: {version or 'unavailable'}"
        )
    expected = {"auto": True, "prune": True, "reserved": 16000}
    if compaction != expected:
        raise ValueError("resolved OpenCode compaction policy does not match the auto-only baseline")


class CompactionRejected(ValueError):
    pass


@dataclass(frozen=True)
class CompactionPolicy:
    enabled: bool = False
    pressure_threshold: float = 0.62
    advisory_threshold: float = 0.50
    minimum_turns_since_compaction: int = 2

    def __post_init__(self) -> None:
        if not 0.0 < self.advisory_threshold <= self.pressure_threshold < 1.0:
            raise ValueError("compaction thresholds must be ordered between zero and one")
        if self.minimum_turns_since_compaction < 0:
            raise ValueError("minimum_turns_since_compaction must be non-negative")


@dataclass(frozen=True)
class CompactionReadiness:
    controller_owned: bool
    thread_idle: bool
    tool_active: bool
    checkpoint_current: bool
    milestone_completed: bool
    terminal_milestone: bool
    remaining_work: bool
    last_input_tokens: int | None
    context_window: int | None
    turns_since_compaction: int

    def __post_init__(self) -> None:
        for name in ("last_input_tokens", "context_window"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or unavailable")
        if self.context_window == 0:
            raise ValueError("context_window must be positive or unavailable")
        if self.turns_since_compaction < 0:
            raise ValueError("turns_since_compaction must be non-negative")


@dataclass(frozen=True)
class CompactionDecision:
    action: str
    reasons: tuple[str, ...]
    pressure: float | None


def evaluate_compaction(
    readiness: CompactionReadiness,
    policy: CompactionPolicy = CompactionPolicy(),
) -> CompactionDecision:
    pressure = None
    if readiness.last_input_tokens is not None and readiness.context_window is not None:
        pressure = readiness.last_input_tokens / readiness.context_window
    reasons: list[str] = []
    if not readiness.controller_owned:
        reasons.append("session-not-controller-owned")
    if not readiness.thread_idle:
        reasons.append("thread-active")
    if readiness.tool_active:
        reasons.append("tool-active")
    if not readiness.checkpoint_current:
        reasons.append("checkpoint-stale")
    if not readiness.milestone_completed:
        reasons.append("milestone-incomplete")
    if readiness.terminal_milestone:
        reasons.append("terminal-milestone")
    if not readiness.remaining_work:
        reasons.append("no-remaining-work")
    if readiness.turns_since_compaction < policy.minimum_turns_since_compaction:
        reasons.append("compaction-cooldown")
    if pressure is None:
        reasons.append("context-unavailable")
    elif pressure < policy.pressure_threshold:
        reasons.append("below-pressure-threshold")

    if not policy.enabled:
        action = "advisory" if pressure is not None and pressure >= policy.advisory_threshold else "disabled"
        return CompactionDecision(action, tuple(reasons or ("candidate-disabled",)), pressure)
    if reasons:
        action = "advisory" if pressure is not None and pressure >= policy.advisory_threshold else "reject"
        return CompactionDecision(action, tuple(reasons), pressure)
    return CompactionDecision("request", (), pressure)


def authorize_compaction(
    readiness: CompactionReadiness,
    policy: CompactionPolicy,
) -> CompactionDecision:
    decision = evaluate_compaction(readiness, policy)
    if decision.action != "request":
        raise CompactionRejected(",".join(decision.reasons) or decision.action)
    return decision


@dataclass(frozen=True)
class CompactionLifecycleEvent:
    record_id: str
    compaction_id: str
    observed_at: str
    state: str
    trigger: str
    surface: str
    run_id: str
    session_id: str
    repository: str
    issue: int
    turn_id: str | None = None
    item_id: str | None = None
    before_input_tokens: int | None = None
    after_input_tokens: int | None = None
    context_window: int | None = None
    error_class: str | None = None
    schema_version: int = COMPACTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("record_id", "compaction_id", "run_id", "session_id"):
            _safe_atom(getattr(self, name), name)
        for name in ("turn_id", "item_id", "error_class"):
            value = getattr(self, name)
            if value is not None:
                _safe_atom(value, name)
        _timestamp(self.observed_at)
        if self.state not in COMPACTION_STATES:
            raise ValueError("invalid compaction state")
        if self.trigger not in COMPACTION_TRIGGERS:
            raise ValueError("invalid compaction trigger")
        if self.surface not in SURFACES:
            raise ValueError("invalid compaction surface")
        if not REPOSITORY_PATTERN.fullmatch(self.repository):
            raise ValueError("repository must be a valid OWNER/REPO identifier")
        if self.issue <= 0:
            raise ValueError("issue must be positive")
        for name in ("before_input_tokens", "after_input_tokens", "context_window"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise ValueError(f"{name} must be a non-negative integer or unavailable")
        if self.context_window == 0:
            raise ValueError("context_window must be positive or unavailable")
        if self.schema_version != COMPACTION_SCHEMA_VERSION:
            raise ValueError("unsupported compaction schema version")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> CompactionLifecycleEvent:
        return cls(**payload)


class CompactionTracker:
    """Normalize content-free Codex app-server compaction lifecycle facts."""

    def __init__(self, *, run_id: str, session_id: str, repository: str, issue: int) -> None:
        self.run_id = run_id
        self.session_id = session_id
        self.repository = repository
        self.issue = issue
        self.compaction_id: str | None = None
        self.trigger: str | None = None
        self.turn_id: str | None = None
        self.item_id: str | None = None
        self.state: str | None = None
        self.before_input_tokens: int | None = None
        self.context_window: int | None = None
        self.awaiting_turn_completion = False

    def requested(
        self, *, compaction_id: str, observed_at: str,
        before_input_tokens: int | None, context_window: int | None,
    ) -> CompactionLifecycleEvent:
        if self.state not in {None, "post-context-observed", "failed"}:
            raise CompactionRejected("compaction-already-in-flight")
        self.compaction_id = compaction_id
        self.trigger = "manual"
        self.state = "requested"
        self.turn_id = self.item_id = None
        self.before_input_tokens = before_input_tokens
        self.context_window = context_window
        return self._record(observed_at=observed_at, state="requested")

    def failed(self, *, observed_at: str, error_class: str) -> CompactionLifecycleEvent:
        if self.state not in {"requested", "started", "completed"}:
            raise CompactionRejected("no-compaction-in-flight")
        self.state = "failed"
        return self._record(
            observed_at=observed_at, state="failed", error_class=error_class,
        )

    def observe(self, notification: dict[str, Any], *, observed_at: str) -> CompactionLifecycleEvent | None:
        method = notification.get("method")
        params = notification.get("params")
        if not isinstance(params, dict) or params.get("threadId") != self.session_id:
            return None
        if method in {"item/started", "item/completed"}:
            item = params.get("item")
            if not isinstance(item, dict) or item.get("type") != "contextCompaction":
                return None
            item_id = item.get("id")
            turn_id = params.get("turnId")
            if not isinstance(item_id, str) or not isinstance(turn_id, str):
                raise ValueError("compaction notification lacks bounded item identity")
            if self.compaction_id is None:
                self.compaction_id = f"auto:{self.session_id}:{item_id}"
                self.trigger = "automatic"
            if self.item_id is not None and self.item_id != item_id:
                raise ValueError("conflicting compaction item identity")
            self.item_id = item_id
            self.turn_id = turn_id
            state = "started" if method == "item/started" else "completed"
            if state == "completed" and self.state not in {"started", "requested"}:
                raise ValueError("compaction completed without a request or start")
            self.state = state
            if state == "completed":
                self.awaiting_turn_completion = True
            return self._record(observed_at=observed_at, state=state)
        if method == "turn/completed" and self.state in {"requested", "started", "completed"}:
            turn = params.get("turn")
            if not isinstance(turn, dict) or turn.get("id") != self.turn_id:
                return None
            if turn.get("status") != "completed":
                self.state = "failed"
                self.awaiting_turn_completion = False
                return self._record(
                    observed_at=observed_at, state="failed", error_class="compaction-turn-failed",
                )
            self.awaiting_turn_completion = False
        if (
            method == "thread/tokenUsage/updated"
            and self.state == "completed"
            and not self.awaiting_turn_completion
        ):
            turn_id = params.get("turnId")
            if not isinstance(turn_id, str) or turn_id == self.turn_id:
                return None
            usage = params.get("tokenUsage")
            last = usage.get("last") if isinstance(usage, dict) else None
            after = last.get("inputTokens") if isinstance(last, dict) else None
            window = usage.get("modelContextWindow") if isinstance(usage, dict) else None
            if not isinstance(after, int) or isinstance(after, bool) or after < 0:
                return None
            if window is not None and (not isinstance(window, int) or isinstance(window, bool) or window <= 0):
                return None
            self.state = "post-context-observed"
            return self._record(
                observed_at=observed_at, state="post-context-observed",
                turn_id=turn_id, after_input_tokens=after,
                context_window=window if window is not None else self.context_window,
            )
        return None

    def _record(
        self, *, observed_at: str, state: str, turn_id: str | None = None,
        after_input_tokens: int | None = None, context_window: int | None = None,
        error_class: str | None = None,
    ) -> CompactionLifecycleEvent:
        if self.compaction_id is None or self.trigger is None:
            raise ValueError("compaction identity is unavailable")
        item = self.item_id or "pending"
        return CompactionLifecycleEvent(
            record_id=f"{self.compaction_id}:{state}:{turn_id or self.turn_id or item}",
            compaction_id=self.compaction_id, observed_at=observed_at,
            state=state, trigger=self.trigger, surface="primary-codex",
            run_id=self.run_id, session_id=self.session_id,
            repository=self.repository, issue=self.issue,
            turn_id=turn_id or self.turn_id, item_id=self.item_id,
            before_input_tokens=self.before_input_tokens,
            after_input_tokens=after_input_tokens,
            context_window=context_window if context_window is not None else self.context_window,
            error_class=error_class,
        )


class OpenCodeCompactionTracker:
    """Normalize the OpenCode 1.18.21 lifecycle without retaining text.

    The `delta` and `ended.text` fields can contain generated summary content,
    so this adapter deliberately reads only event identity, session identity,
    timestamp, and the automatic/manual reason.
    """

    def __init__(
        self, *, run_id: str, session_id: str, repository: str, issue: int,
    ) -> None:
        self.run_id = run_id
        self.session_id = session_id
        self.repository = repository
        self.issue = issue
        self.compaction_id: str | None = None
        self.trigger: str | None = None
        self.state: str | None = None
        self._sequence = 0

    def observe(
        self, event: dict[str, Any], *, observed_at: str,
    ) -> CompactionLifecycleEvent | None:
        event_type = event.get("type")
        properties = event.get("properties")
        if not isinstance(properties, dict) or properties.get("sessionID") != self.session_id:
            return None
        if event_type == "session.next.compaction.delta":
            return None
        if event_type == "session.next.compaction.started":
            reason = properties.get("reason")
            if reason not in {"auto", "manual"}:
                raise ValueError("OpenCode compaction start lacks a valid reason")
            event_id = event.get("id")
            if event_id is not None and not isinstance(event_id, str):
                raise ValueError("OpenCode compaction event id must be a string")
            self._sequence += 1
            identity = f"{self.run_id}:{self.session_id}:{event_id or self._sequence}"
            suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
            self.compaction_id = f"opencode:{suffix}"
            self.trigger = "automatic" if reason == "auto" else "manual"
            self.state = "started"
            return self._record(observed_at, "started")
        if event_type in {"session.next.compaction.ended", "session.compacted"}:
            if self.state == "completed":
                return None
            if self.compaction_id is None:
                # The legacy compacted event carries no event or trigger identity.
                # It is still an observed automatic lifecycle fact for the exact
                # bridge-owned session; manual calls are disabled by default.
                self._sequence += 1
                identity = f"{self.run_id}:{self.session_id}:legacy:{self._sequence}"
                suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
                self.compaction_id = f"opencode:{suffix}"
                self.trigger = "automatic"
            self.state = "completed"
            return self._record(observed_at, "completed")
        if event_type == "session.error" and self.state == "started":
            self.state = "failed"
            return self._record(observed_at, "failed", error_class="opencode-session-error")
        return None

    def _record(
        self, observed_at: str, state: str, *, error_class: str | None = None,
    ) -> CompactionLifecycleEvent:
        if self.compaction_id is None or self.trigger is None:
            raise ValueError("OpenCode compaction identity is unavailable")
        return CompactionLifecycleEvent(
            record_id=f"{self.compaction_id}:{state}",
            compaction_id=self.compaction_id,
            observed_at=observed_at,
            state=state,
            trigger=self.trigger,
            surface="local-opencode",
            run_id=self.run_id,
            session_id=self.session_id,
            repository=self.repository,
            issue=self.issue,
            error_class=error_class,
        )


def append_compaction_record(path: Path, record: CompactionLifecycleEvent) -> bool:
    """Append a compaction record; exact replay is a no-op and conflicts fail."""
    from .diagnostics import append_record
    return append_record(path, record)


def read_compaction_records(path: Path) -> tuple[CompactionLifecycleEvent, ...]:
    """Read all compaction records from a JSONL file."""
    from .diagnostics import read_records
    return read_records(path, CompactionLifecycleEvent)
