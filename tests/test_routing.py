from __future__ import annotations

import pytest

from codex_local_harness.routing import Disposition, classify_issue


@pytest.mark.parametrize(
    "text",
    [
        "Change OAuth authentication",
        "Store an API secret securely",
        "Migrate the production schema",
        "Preserve public API backward compatibility",
        "Delete data with possible data loss",
        "Add a significant new dependency",
        "Resolve conflicting requirements",
    ],
)
def test_high_risk_work_stays_with_codex(text):
    decision = classify_issue(text, text, [])
    assert decision.disposition is Disposition.CODEX_ONLY
    assert decision.maximum_correction_cycles == 0
    assert decision.reasons


@pytest.mark.parametrize(
    ("text", "focus"),
    [
        ("Validate retry-delay boundaries including NaN and infinity", "boundaries"),
        ("Parse malformed numeric input", "parsing-and-validation"),
        ("Make increments atomic across concurrent processes", "concurrency"),
        ("Use an injectable monotonic clock for TTL expiry", "time"),
        ("Deduplicate nested unhashable values by equality", "identity-and-equality"),
        ("Clean temporary files after a partial write", "cleanup-and-failure"),
    ],
)
def test_boundary_heavy_work_gets_one_critic_cycle(text, focus):
    decision = classify_issue(text, text, [])
    assert decision.disposition is Disposition.LOCAL_WITH_CRITIC
    assert focus in decision.critic_focus
    assert decision.maximum_correction_cycles == 1


def test_ordinary_bounded_work_skips_preflight_and_critic():
    decision = classify_issue(
        "Update footer wording",
        "Change the documentation sentence from alpha to beta.",
        ["README contains the new sentence", "Tests continue to pass"],
    )
    assert decision.disposition is Disposition.LOCAL_DIRECT
    assert decision.critic_focus == ()
    assert decision.maximum_correction_cycles == 0


def test_escalation_takes_precedence_over_critic_keywords():
    decision = classify_issue(
        "Parse OAuth tokens",
        "Add boundary validation around authentication tokens.",
        ["Reject malformed values"],
    )
    assert decision.disposition is Disposition.CODEX_ONLY
    assert "authentication-or-authorization" in decision.reasons


def test_acceptance_criteria_participate_in_routing():
    decision = classify_issue("Update helper", "Small implementation.", ["Must be safe under concurrent threads"])
    assert decision.disposition is Disposition.LOCAL_WITH_CRITIC
    assert "concurrency" in decision.critic_focus


def test_case6_semantic_contradiction_is_explicitly_detected():
    decision = classify_issue(
        "Make service-token matching user friendly without changing security semantics",
        "Match case-insensitively and ignore surrounding whitespace while preserving the existing exact-match, case-sensitive behavior unchanged.",
        ["Authentication security is not weakened"],
    )
    assert decision.disposition is Disposition.CODEX_ONLY
    assert "authentication-or-authorization" in decision.reasons
    assert "conflicting-requirements" in decision.reasons
