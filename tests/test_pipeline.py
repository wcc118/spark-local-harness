from __future__ import annotations

import pytest

from codex_local_harness.pipeline import Action, coder_completed, critic_completed, reviewer_completed, start
from codex_local_harness.routing import Disposition, RoutingDecision


SHA1 = "a" * 40
SHA2 = "b" * 40


def decision(disposition, cycles=0):
    return RoutingDecision(1, disposition, ("test",), ("boundaries",), cycles)


def test_direct_route_never_runs_critic():
    state = start(decision(Disposition.LOCAL_DIRECT))
    state = coder_completed(state, SHA1)
    assert state.action is Action.REVIEWER
    state = reviewer_completed(state, "APPROVE", SHA1)
    assert state.action is Action.COMPLETE


def test_selective_critic_passes_to_reviewer():
    state = start(decision(Disposition.LOCAL_WITH_CRITIC, 1))
    state = coder_completed(state, SHA1)
    assert state.action is Action.CRITIC
    state = critic_completed(state, "PASS", SHA1)
    assert state.action is Action.REVIEWER


def test_one_correction_cycle_then_recritic_and_review():
    state = coder_completed(start(decision(Disposition.LOCAL_WITH_CRITIC, 1)), SHA1)
    state = critic_completed(state, "FINDINGS", SHA1)
    assert state.action is Action.CORRECTION
    assert state.correction_cycles == 1
    state = coder_completed(state, SHA2)
    assert state.action is Action.CRITIC
    state = critic_completed(state, "PASS", SHA2)
    state = reviewer_completed(state, "APPROVE", SHA2)
    assert state.action is Action.COMPLETE


def test_second_finding_escalates_instead_of_looping():
    state = coder_completed(start(decision(Disposition.LOCAL_WITH_CRITIC, 1)), SHA1)
    state = critic_completed(state, "FINDINGS", SHA1)
    state = coder_completed(state, SHA2)
    state = critic_completed(state, "FINDINGS", SHA2)
    assert state.action is Action.CODEX
    assert state.reason == "correction limit reached"


def test_high_risk_route_never_runs_local_worker():
    state = start(decision(Disposition.CODEX_ONLY))
    assert state.action is Action.CODEX


@pytest.mark.parametrize("stage", ["critic", "reviewer"])
def test_stale_sha_fails_closed(stage):
    state = coder_completed(start(decision(Disposition.LOCAL_WITH_CRITIC, 1)), SHA1)
    if stage == "critic":
        state = critic_completed(state, "PASS", SHA2)
    else:
        state = critic_completed(state, "PASS", SHA1)
        state = reviewer_completed(state, "APPROVE", SHA2)
    assert state.action is Action.BLOCKED
    assert "stale head" in state.reason


def test_reviewer_nonapproval_returns_to_codex_not_coder_loop():
    state = coder_completed(start(decision(Disposition.LOCAL_DIRECT)), SHA1)
    state = reviewer_completed(state, "REQUEST_CHANGES", SHA1)
    assert state.action is Action.CODEX


def test_invalid_transition_raises():
    with pytest.raises(ValueError):
        reviewer_completed(start(decision(Disposition.LOCAL_DIRECT)), "APPROVE", SHA1)
