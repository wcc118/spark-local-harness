import json

import pytest

from codex_local_harness.evidence import ArtifactEvidence, CheckEvidence, EvidenceBundle, ScoutFinding
from codex_local_harness.merge_gate import (
    MergeDecision,
    MergePreflight,
    deterministic_blockers,
    make_decision,
    prepare_packet,
    write_decision,
)


def evidence(**changes):
    values = {
        "repository": "owner/repo",
        "issue": 9,
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
        "acceptance_criteria": ("Return the correct value",),
        "changed_files": ("src/value.py",),
        "diff_summary": "One implementation file changed",
        "tests": (CheckEvidence("pytest -q", 0, "8 passed"),),
        "artifacts": ArtifactEvidence(tracked_count=1),
        "worker_run_id": "run-9",
    }
    values.update(changes)
    return EvidenceBundle(**values)


def preflight(**changes):
    values = {
        "current_head_sha": "b" * 40,
        "checkout_clean": True,
        "scope_valid": True,
        "required_checks_complete": True,
    }
    values.update(changes)
    return MergePreflight(**values)


def test_clean_packet_has_no_blockers_and_is_digest_bound():
    packet = prepare_packet(evidence(), preflight())
    assert packet["deterministic_blockers"] == []
    assert len(packet["evidence_sha256"]) == 64
    assert packet["head_sha"] == "b" * 40


def test_preflight_collects_all_deterministic_blockers():
    finding = ScoutFinding("high", "AC-1", "Incorrect behavior", "Reproduction", 0.95)
    item = evidence(
        tests=(CheckEvidence("pytest -q", 1, "1 failed"),),
        unresolved_questions=("Does this preserve identity?",),
        scout_findings=(finding,),
    )
    blockers = deterministic_blockers(
        item,
        preflight(
            current_head_sha="c" * 40,
            checkout_clean=False,
            scope_valid=False,
            required_checks_complete=False,
            protected_risk=True,
        ),
    )
    assert blockers == (
        "stale-head",
        "dirty-checkout",
        "scope-violation",
        "required-checks-incomplete",
        "tests-not-passing",
        "unresolved-questions",
        "unresolved-material-finding",
        "protected-risk-requires-intervention",
    )


def test_merge_is_refused_while_a_blocker_remains():
    with pytest.raises(ValueError, match="MERGE is prohibited"):
        make_decision(
            evidence(),
            preflight(checkout_clean=False),
            MergeDecision.MERGE,
            ("Looks correct",),
            decided_by="codex",
            decided_at="2026-08-15T16:00:00Z",
        )


def test_only_codex_can_record_decisions():
    with pytest.raises(ValueError, match="only codex"):
        make_decision(
            evidence(),
            preflight(),
            MergeDecision.MERGE,
            ("Looks correct",),
            decided_by="local-reviewer",
            decided_at="2026-08-15T16:00:00Z",
        )


def test_non_merge_decision_preserves_blockers_and_writes_atomically(tmp_path):
    record = make_decision(
        evidence(),
        preflight(scope_valid=False),
        MergeDecision.LOCAL_CORRECTION,
        ("Return the implementation to declared scope",),
        decided_by="codex",
        decided_at="2026-08-15T16:00:00Z",
    )
    path = tmp_path / "decision.json"
    write_decision(path, record)
    payload = json.loads(path.read_text())
    assert payload["decision"] == "local-correction"
    assert payload["deterministic_blockers"] == ["scope-violation"]
    assert payload["reviewed_head_sha"] == "b" * 40

