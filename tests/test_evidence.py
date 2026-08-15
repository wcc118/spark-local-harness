import pytest

from codex_local_harness.evidence import (
    ArtifactEvidence,
    EvidenceBundle,
    ScoutFinding,
    CheckEvidence,
)


def bundle(**changes):
    values = {
        "repository": "owner/repo",
        "issue": 3,
        "base_sha": "a" * 40,
        "head_sha": "b" * 40,
        "acceptance_criteria": ("Preserve behavior",),
        "changed_files": ("src/example.py",),
        "diff_summary": "One file changed",
        "tests": (CheckEvidence("pytest -q", 0, "4 passed"),),
        "artifacts": ArtifactEvidence(tracked_count=1),
    }
    values.update(changes)
    return EvidenceBundle(**values)


def test_bundle_is_head_bound_and_reports_test_status():
    evidence = bundle()
    assert evidence.tests_passed is True
    assert evidence.as_dict()["head_sha"] == "b" * 40
    assert evidence.as_dict()["tests_passed"] is True


def test_bundle_requires_full_lowercase_shas():
    with pytest.raises(ValueError, match="full lowercase"):
        bundle(head_sha="ABC")


def test_artifact_sample_is_bounded():
    with pytest.raises(ValueError, match="exceeds 25"):
        ArtifactEvidence(generated_count=26, sample=tuple(f"file-{i}" for i in range(26)))


def test_scout_finding_validates_confidence_and_location():
    finding = ScoutFinding("high", "AC-1", "Race exists", "Unlocked write", 0.9, "counter.py", 9)
    assert finding.line == 9
    with pytest.raises(ValueError, match="confidence"):
        ScoutFinding("high", "AC-1", "Race exists", "Unlocked write", 1.1)
