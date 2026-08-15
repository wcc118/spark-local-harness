from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from .routing import Disposition, RoutingDecision


class Action(str, Enum):
    CODEX = "codex"
    CODER = "coder"
    CRITIC = "critic"
    CORRECTION = "correction"
    REVIEWER = "reviewer"
    COMPLETE = "complete"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class PipelineState:
    routing: RoutingDecision
    action: Action
    correction_cycles: int = 0
    head_sha: str | None = None
    critic_verdict: str | None = None
    reviewer_verdict: str | None = None
    reason: str | None = None


def start(decision: RoutingDecision) -> PipelineState:
    if decision.disposition is Disposition.CODEX_ONLY:
        return PipelineState(decision, Action.CODEX, reason=",".join(decision.reasons))
    return PipelineState(decision, Action.CODER)


def coder_completed(state: PipelineState, head_sha: str) -> PipelineState:
    if state.action not in {Action.CODER, Action.CORRECTION}:
        raise ValueError(f"coder completion invalid from {state.action}")
    if not head_sha:
        raise ValueError("coder completion requires a head SHA")
    if state.action is Action.CORRECTION:
        return replace(state, action=Action.CRITIC, head_sha=head_sha, critic_verdict=None)
    next_action = Action.CRITIC if state.routing.disposition is Disposition.LOCAL_WITH_CRITIC else Action.REVIEWER
    return replace(state, action=next_action, head_sha=head_sha)


def critic_completed(state: PipelineState, verdict: str, reviewed_head_sha: str) -> PipelineState:
    if state.action is not Action.CRITIC:
        raise ValueError(f"critic completion invalid from {state.action}")
    if reviewed_head_sha != state.head_sha:
        return replace(state, action=Action.BLOCKED, reason="critic reviewed stale head")
    if verdict == "PASS":
        return replace(state, action=Action.REVIEWER, critic_verdict=verdict)
    if verdict == "ESCALATE":
        return replace(state, action=Action.CODEX, critic_verdict=verdict, reason="critic escalation")
    if verdict != "FINDINGS":
        return replace(state, action=Action.BLOCKED, reason="invalid critic verdict")
    if state.correction_cycles >= state.routing.maximum_correction_cycles:
        return replace(state, action=Action.CODEX, critic_verdict=verdict, reason="correction limit reached")
    return replace(
        state,
        action=Action.CORRECTION,
        critic_verdict=verdict,
        correction_cycles=state.correction_cycles + 1,
    )


def reviewer_completed(state: PipelineState, verdict: str, reviewed_head_sha: str) -> PipelineState:
    if state.action is not Action.REVIEWER:
        raise ValueError(f"reviewer completion invalid from {state.action}")
    if reviewed_head_sha != state.head_sha:
        return replace(state, action=Action.BLOCKED, reason="reviewer reviewed stale head")
    if verdict == "APPROVE":
        return replace(state, action=Action.COMPLETE, reviewer_verdict=verdict)
    if verdict in {"REQUEST_CHANGES", "ESCALATE"}:
        return replace(state, action=Action.CODEX, reviewer_verdict=verdict, reason="reviewer did not approve")
    return replace(state, action=Action.BLOCKED, reason="invalid reviewer verdict")

