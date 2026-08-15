import json

import pytest

from codex_local_harness.roles import (
    AdvisoryRole,
    FailureAnalysisResult,
    MapperResult,
    ScoutResult,
    DesignedTestsResult,
    parse_result,
    normalize_single_json_fence,
    role_contract,
)


def test_role_contracts_deny_authority_and_require_json():
    for role in AdvisoryRole:
        contract = role_contract(role)
        assert "advisory only" in contract
        assert "Do not edit files" in contract
        assert "approve, or merge" in contract
        assert "exactly one JSON object" in contract


def test_mapper_parses_bounded_relative_evidence():
    payload = {
        "schema_version": 1,
        "relevant_files": ["src/value.py"],
        "symbols": ["value"],
        "existing_tests": ["tests/test_value.py"],
        "constraints": ["Preserve API"],
        "uncertainties": [],
        "recommended_scope": ["Change src/value.py only"],
    }
    result = parse_result(AdvisoryRole.MAPPER, json.dumps(payload))
    assert isinstance(result, MapperResult)
    assert result.relevant_files == ("src/value.py",)
    payload["relevant_files"] = ["../secret"]
    with pytest.raises(ValueError, match="repository-relative"):
        parse_result(AdvisoryRole.MAPPER, json.dumps(payload))


def test_test_designer_requires_at_least_one_complete_test():
    payload = {
        "schema_version": 1,
        "tests": [{"name": "empty", "criterion": "AC-1", "setup": "empty input", "assertion": "returns []", "rationale": "boundary"}],
        "unresolved_questions": [],
    }
    assert isinstance(parse_result(AdvisoryRole.TEST_DESIGNER, json.dumps(payload)), DesignedTestsResult)
    payload["tests"] = []
    with pytest.raises(ValueError, match="at least one"):
        parse_result(AdvisoryRole.TEST_DESIGNER, json.dumps(payload))


def test_failure_analysis_validates_confidence():
    payload = {
        "schema_version": 1,
        "hypotheses": [{"claim": "race", "evidence": "lost update", "confidence": 0.8, "next_check": "run concurrently"}],
        "known_facts": ["single-thread test passes"],
        "unresolved_questions": [],
    }
    assert isinstance(parse_result(AdvisoryRole.FAILURE_ANALYST, json.dumps(payload)), FailureAnalysisResult)
    payload["hypotheses"][0]["confidence"] = 2
    with pytest.raises(ValueError, match="confidence"):
        parse_result(AdvisoryRole.FAILURE_ANALYST, json.dumps(payload))


def test_scout_accepts_no_findings_but_rejects_bad_location():
    payload = {"schema_version": 1, "findings": [], "unresolved_questions": []}
    assert isinstance(parse_result(AdvisoryRole.SCOUT, json.dumps(payload)), ScoutResult)
    payload["findings"] = [{
        "severity": "high", "criterion": "AC-2", "claim": "wrong", "evidence": "repro",
        "confidence": 0.9, "suggested_validation": "add test", "file": "/etc/passwd", "line": 1,
    }]
    with pytest.raises(ValueError, match="repository-relative"):
        parse_result(AdvisoryRole.SCOUT, json.dumps(payload))


def test_parser_rejects_markdown_fences_and_extra_prose():
    with pytest.raises(ValueError, match="exactly one JSON"):
        parse_result(AdvisoryRole.SCOUT, "```json\n{}\n```")


def test_normalizer_accepts_one_json_fence_but_no_surrounding_prose():
    assert normalize_single_json_fence('```json\n{"findings": []}\n```') == '{"findings": []}'
    with pytest.raises(ValueError, match="exactly one"):
        normalize_single_json_fence('Here it is:\n```json\n{}\n```')
