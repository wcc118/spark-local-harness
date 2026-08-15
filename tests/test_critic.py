from __future__ import annotations

import json

import pytest

from codex_local_harness.critic import (
    RESULT_PREFIX,
    actionable_findings,
    parse_result,
    prepare_prompt,
    validate_result,
)
from codex_local_harness.review import Envelope, ReviewError


SHA = "a" * 40


def envelope(tmp_path):
    return Envelope(1, "owner/repo", 3, 5, str(tmp_path), "issue-3", SHA, "b" * 40, "main")


def result(**changes):
    value = {
        "schema_version": 1,
        "verdict": "PASS",
        "repository": "owner/repo",
        "issue": 3,
        "pull_request": 5,
        "reviewed_head_sha": SHA,
        "focus": ["boundaries"],
        "findings": [],
        "suggested_tests": [],
        "summary": "No defect found.",
        "confidence": "high",
        "escalation_reason": None,
    }
    value.update(changes)
    return value


def test_prepare_prompt_binds_identity_and_focus(tmp_path):
    prompt = prepare_prompt(envelope(tmp_path), "inspect", ["boundaries", "time"])
    assert SHA in prompt
    assert 'CRITIC_FOCUS=["boundaries","time"]' in prompt
    assert RESULT_PREFIX in prompt


def test_parse_exactly_one_result(tmp_path):
    log = tmp_path / "log.jsonl"
    event = {"type": "text", "part": {"text": RESULT_PREFIX + json.dumps(result())}}
    log.write_text(json.dumps(event) + "\n", encoding="utf-8")
    assert parse_result(log)["verdict"] == "PASS"


def test_parse_accepts_compaction_repetition_with_same_decision(tmp_path):
    log = tmp_path / "log.jsonl"
    first = result(
        verdict="FINDINGS",
        findings=[{"id": "F1", "severity": "medium", "file": "a.py", "line": 3, "description": "long"}],
        summary="first wording",
    )
    second = result(
        verdict="FINDINGS",
        findings=[{"id": "F1", "severity": "medium", "path": "a.py", "line": 3, "description": "short"}],
        summary="second wording",
    )
    events = [
        {"type": "text", "part": {"text": RESULT_PREFIX + json.dumps(first)}},
        {"type": "text", "part": {"text": RESULT_PREFIX + json.dumps(second)}},
    ]
    log.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    assert parse_result(log)["summary"] == "second wording"


@pytest.mark.parametrize(
    "change",
    [
        {"verdict": "ESCALATE", "escalation_reason": "conflict"},
        {"findings": [{"id": "F1", "severity": "high", "path": "a.py", "line": 3}]},
        {"findings": []},
    ],
)
def test_parse_rejects_conflicting_repeated_decisions(tmp_path, change):
    log = tmp_path / "log.jsonl"
    first = result(verdict="FINDINGS", findings=[{"id": "F1", "severity": "medium", "path": "a.py", "line": 3}])
    second = dict(first)
    second.update(change)
    events = [
        {"type": "text", "part": {"text": RESULT_PREFIX + json.dumps(first)}},
        {"type": "text", "part": {"text": RESULT_PREFIX + json.dumps(second)}},
    ]
    log.write_text("\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8")
    with pytest.raises(ReviewError, match="conflicting critic results"):
        parse_result(log)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"verdict": "PASS", "findings": [{"id": "F1", "severity": "major", "message": "bug"}]}, "cannot contain"),
        ({"verdict": "FINDINGS", "findings": []}, "requires at least one"),
        ({"verdict": "ESCALATE", "escalation_reason": None}, "requires an escalation"),
    ],
)
def test_validate_critic_result_fails_closed(tmp_path, changes, message):
    with pytest.raises(ReviewError, match=message):
        validate_result(result(**changes), envelope(tmp_path))


def test_validate_findings_and_escalation(tmp_path):
    findings = result(verdict="FINDINGS", findings=[{"id": "F1", "severity": "major", "message": "bug"}])
    assert validate_result(findings, envelope(tmp_path))["verdict"] == "FINDINGS"
    assert actionable_findings(findings)[0]["id"] == "F1"
    escalation = result(verdict="ESCALATE", escalation_reason="conflicting requirements")
    assert validate_result(escalation, envelope(tmp_path))["verdict"] == "ESCALATE"


def test_defect_verdict_is_normalized_to_findings(tmp_path):
    value = result(verdict="defect", findings=[{"id": "F1", "severity": "high", "message": "bug"}])
    assert validate_result(value, envelope(tmp_path))["verdict"] == "FINDINGS"


def test_comma_separated_focus_is_normalized(tmp_path):
    assert validate_result(result(focus="identity,equality"), envelope(tmp_path))["focus"] == ["identity", "equality"]


@pytest.mark.parametrize("version", [1, "1", "1.0", 2, None])
def test_schema_is_owned_and_normalized_by_harness(tmp_path, version):
    validated = validate_result(result(schema_version=version), envelope(tmp_path))
    assert validated["schema_version"] == 1


def test_supplied_critic_identity_is_replaced_by_envelope(tmp_path):
    validated = validate_result(
        result(repository="checkout-name", issue="#3", pull_request="#5", reviewed_head_sha=None),
        envelope(tmp_path),
    )
    assert validated["repository"] == "owner/repo"
    assert validated["issue"] == 3
    assert validated["pull_request"] == 5
    assert validated["reviewed_head_sha"] == SHA


def test_missing_immutable_fields_are_hydrated_from_envelope(tmp_path):
    value = result(
        verdict="FINDINGS",
        findings=[{"severity": "high", "path": "a.py", "line": 2, "message": "race"}],
    )
    for field in ("schema_version", "repository", "issue", "pull_request", "reviewed_head_sha", "focus", "escalation_reason"):
        value.pop(field)
    validated = validate_result(value, envelope(tmp_path))
    assert validated["repository"] == "owner/repo"
    assert validated["reviewed_head_sha"] == SHA
    assert validated["findings"][0]["id"] == "F1"


def test_low_only_findings_do_not_trigger_correction(tmp_path):
    low = result(verdict="FINDINGS", findings=[{"id": "F1", "severity": "low", "message": "dead code"}])
    with pytest.raises(ReviewError, match="actionable"):
        validate_result(low, envelope(tmp_path))


def test_duplicate_finding_ids_are_rejected(tmp_path):
    duplicate = result(
        verdict="FINDINGS",
        findings=[
            {"id": "F1", "severity": "major", "message": "one"},
            {"id": "F1", "severity": "medium", "message": "two"},
        ],
    )
    with pytest.raises(ReviewError, match="duplicate"):
        validate_result(duplicate, envelope(tmp_path))
