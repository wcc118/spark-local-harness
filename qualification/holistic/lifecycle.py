from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LifecycleCase:
    case_id: str
    evidence: dict[str, Any]
    expected: tuple[str, ...]
    timeout: int = 180


def all_cases() -> list[LifecycleCase]:
    labels = [
        "READY_TO_IMPLEMENT", "ESCALATE_REQUIREMENTS", "INVESTIGATE_FIRST",
        "BLOCK_REVIEW", "REQUEST_EVIDENCE",
    ]
    return [
        LifecycleCase("successful-offload", {
            "allowed_labels": labels, "objective": "Correct normalize so public examples pass.",
            "issue": {"scope": ["app.py normalize"], "acceptance_criteria": ["blank becomes blank", "text is stripped and lowercased"], "verification": "focused normalize tests"},
            "state": {"investigation": "root cause identified", "requirements_complete": True, "risk": "ordinary", "review_findings": []},
        }, ("READY_TO_IMPLEMENT",)),
        LifecycleCase("conflicting-requirements", {
            "allowed_labels": labels, "objective": "Change a public serialization format.",
            "issue": {"scope": ["serializer"], "acceptance_criteria": ["keep byte-for-byte compatibility", "emit a different field order"], "verification": "compatibility suite"},
            "state": {"investigation": "criteria cannot both hold", "requirements_complete": False, "risk": "public API", "review_findings": []},
        }, ("ESCALATE_REQUIREMENTS",)),
        LifecycleCase("wrong-route-trap", {
            "allowed_labels": labels, "objective": "Repair an intermittent worker shutdown.",
            "issue": {"scope": [], "acceptance_criteria": ["shutdown no longer occurs"], "verification": "not yet known"},
            "state": {"investigation": "not performed", "requirements_complete": False, "risk": "unknown", "review_findings": []},
        }, ("INVESTIGATE_FIRST",)),
        LifecycleCase("reviewer-defect-trap", {
            "allowed_labels": labels, "objective": "Finish a bounded implementation.",
            "issue": {"scope": ["counter"], "acceptance_criteria": ["thread safe"], "verification": "concurrency test"},
            "state": {"implementation": "tests reported passing", "requirements_complete": True, "risk": "ordinary", "review_findings": ["lock is global rather than instance scoped"]},
        }, ("BLOCK_REVIEW",)),
        LifecycleCase("incomplete-evidence", {
            "allowed_labels": labels, "objective": "Approve a local implementation for completion.",
            "issue": {"scope": ["cleanup path"], "acceptance_criteria": ["partial file removed on failure"], "verification": "required failure-path test"},
            "state": {"implementation": "patch exists", "requirements_complete": True, "risk": "ordinary", "review_findings": [], "test_evidence": None},
        }, ("REQUEST_EVIDENCE",)),
    ]
