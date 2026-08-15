from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .evidence import EvidenceBundle


SCHEMA_VERSION = 1
MATERIAL_SEVERITIES = {"critical", "high"}


class MergeDecision(str, Enum):
    MERGE = "merge"
    LOCAL_CORRECTION = "local-correction"
    CODEX_INTERVENTION = "codex-intervention"
    BLOCK = "block"


@dataclass(frozen=True)
class MergePreflight:
    current_head_sha: str
    checkout_clean: bool
    scope_valid: bool
    required_checks_complete: bool
    protected_risk: bool = False


@dataclass(frozen=True)
class DecisionRecord:
    repository: str
    issue: int
    reviewed_head_sha: str
    evidence_sha256: str
    decision: MergeDecision
    reasons: tuple[str, ...]
    decided_by: str
    decided_at: str
    deterministic_blockers: tuple[str, ...]
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported merge decision schema version")
        if self.decided_by != "codex":
            raise ValueError("only codex may record a merge-gate decision")
        if not self.reasons or any(not reason.strip() for reason in self.reasons):
            raise ValueError("at least one non-empty decision reason is required")
        if self.decision is MergeDecision.MERGE and self.deterministic_blockers:
            raise ValueError("MERGE is prohibited while deterministic blockers remain")

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["decision"] = self.decision.value
        return result


def evidence_digest(evidence: EvidenceBundle) -> str:
    encoded = json.dumps(evidence.as_dict(), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def deterministic_blockers(evidence: EvidenceBundle, preflight: MergePreflight) -> tuple[str, ...]:
    blockers: list[str] = []
    if preflight.current_head_sha != evidence.head_sha:
        blockers.append("stale-head")
    if not preflight.checkout_clean:
        blockers.append("dirty-checkout")
    if not preflight.scope_valid:
        blockers.append("scope-violation")
    if not preflight.required_checks_complete:
        blockers.append("required-checks-incomplete")
    if not evidence.tests_passed:
        blockers.append("tests-not-passing")
    if evidence.unresolved_questions:
        blockers.append("unresolved-questions")
    if any(finding.severity.lower() in MATERIAL_SEVERITIES for finding in evidence.scout_findings):
        blockers.append("unresolved-material-finding")
    if preflight.protected_risk:
        blockers.append("protected-risk-requires-intervention")
    return tuple(blockers)


def prepare_packet(evidence: EvidenceBundle, preflight: MergePreflight) -> dict[str, Any]:
    blockers = deterministic_blockers(evidence, preflight)
    return {
        "schema_version": SCHEMA_VERSION,
        "repository": evidence.repository,
        "issue": evidence.issue,
        "base_sha": evidence.base_sha,
        "head_sha": evidence.head_sha,
        "evidence_sha256": evidence_digest(evidence),
        "acceptance_criteria": list(evidence.acceptance_criteria),
        "changed_files": list(evidence.changed_files),
        "diff_summary": evidence.diff_summary,
        "tests": [asdict(test) for test in evidence.tests],
        "tests_passed": evidence.tests_passed,
        "artifacts": asdict(evidence.artifacts),
        "scout_findings": [asdict(finding) for finding in evidence.scout_findings],
        "unresolved_questions": list(evidence.unresolved_questions),
        "worker_run_id": evidence.worker_run_id,
        "preflight": asdict(preflight),
        "deterministic_blockers": list(blockers),
        "allowed_decisions": [decision.value for decision in MergeDecision],
    }


def make_decision(
    evidence: EvidenceBundle,
    preflight: MergePreflight,
    decision: MergeDecision,
    reasons: tuple[str, ...],
    *,
    decided_by: str,
    decided_at: str,
) -> DecisionRecord:
    return DecisionRecord(
        repository=evidence.repository,
        issue=evidence.issue,
        reviewed_head_sha=evidence.head_sha,
        evidence_sha256=evidence_digest(evidence),
        decision=decision,
        reasons=reasons,
        decided_by=decided_by,
        decided_at=decided_at,
        deterministic_blockers=deterministic_blockers(evidence, preflight),
    )


def write_decision(path: Path, record: DecisionRecord) -> None:
    """Atomically persist a decision bound to one evidence digest and head SHA."""
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(record.as_dict(), indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise

