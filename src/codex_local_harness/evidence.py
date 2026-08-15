from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any


SCHEMA_VERSION = 1
MAX_ARTIFACT_SAMPLE = 25
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class CheckEvidence:
    command: str
    exit_code: int
    summary: str

    def __post_init__(self) -> None:
        if not self.command.strip() or not self.summary.strip():
            raise ValueError("test command and summary are required")


@dataclass(frozen=True)
class ArtifactEvidence:
    tracked_count: int = 0
    untracked_count: int = 0
    generated_count: int = 0
    sample: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if min(self.tracked_count, self.untracked_count, self.generated_count) < 0:
            raise ValueError("artifact counts must be non-negative")
        if len(self.sample) > MAX_ARTIFACT_SAMPLE:
            raise ValueError(f"artifact sample exceeds {MAX_ARTIFACT_SAMPLE} entries")


@dataclass(frozen=True)
class ScoutFinding:
    severity: str
    criterion: str
    claim: str
    evidence: str
    confidence: float
    file: str | None = None
    line: int | None = None

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.severity, self.criterion, self.claim, self.evidence)):
            raise ValueError("finding fields must be non-empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between zero and one")
        if self.line is not None and self.line <= 0:
            raise ValueError("line must be positive")


@dataclass(frozen=True)
class EvidenceBundle:
    repository: str
    issue: int
    base_sha: str
    head_sha: str
    acceptance_criteria: tuple[str, ...]
    changed_files: tuple[str, ...]
    diff_summary: str
    tests: tuple[CheckEvidence, ...]
    artifacts: ArtifactEvidence
    scout_findings: tuple[ScoutFinding, ...] = ()
    unresolved_questions: tuple[str, ...] = ()
    worker_run_id: str | None = None
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("unsupported evidence schema version")
        if "/" not in self.repository or self.issue <= 0:
            raise ValueError("repository and positive issue are required")
        if not SHA_PATTERN.fullmatch(self.base_sha) or not SHA_PATTERN.fullmatch(self.head_sha):
            raise ValueError("base and head must be full lowercase Git SHAs")
        if not self.acceptance_criteria or any(not item.strip() for item in self.acceptance_criteria):
            raise ValueError("at least one acceptance criterion is required")
        if not self.diff_summary.strip():
            raise ValueError("diff summary is required")
        if len(set(self.changed_files)) != len(self.changed_files):
            raise ValueError("changed files must be unique")

    @property
    def tests_passed(self) -> bool:
        return bool(self.tests) and all(test.exit_code == 0 for test in self.tests)

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["tests_passed"] = self.tests_passed
        return result
