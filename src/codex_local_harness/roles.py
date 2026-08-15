from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath
from typing import Any


SCHEMA_VERSION = 1
MAX_ITEMS = 100


class AdvisoryRole(str, Enum):
    MAPPER = "mapper"
    TEST_DESIGNER = "test-designer"
    FAILURE_ANALYST = "failure-analyst"
    SCOUT = "scout"


ROLE_CONTRACTS = {
    AdvisoryRole.MAPPER: "Map only the repository evidence relevant to the bounded task.",
    AdvisoryRole.TEST_DESIGNER: "Design tests that could falsify the stated acceptance criteria.",
    AdvisoryRole.FAILURE_ANALYST: "Diagnose observed failures and propose evidence-gathering checks.",
    AdvisoryRole.SCOUT: "Search the supplied diff and evidence for concrete defects; do not approve it.",
}


def role_contract(role: AdvisoryRole) -> str:
    return (
        f"{ROLE_CONTRACTS[role]} Return exactly one JSON object matching schema version {SCHEMA_VERSION}. "
        "You are advisory only. Do not edit files, invoke publication tools, change workflow state, "
        "delegate, select other work, redefine acceptance criteria, approve, or merge."
    )


@dataclass(frozen=True)
class MapperResult:
    relevant_files: tuple[str, ...]
    symbols: tuple[str, ...]
    existing_tests: tuple[str, ...]
    constraints: tuple[str, ...]
    uncertainties: tuple[str, ...]
    recommended_scope: tuple[str, ...]
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _version(self.schema_version)
        _bounded("relevant_files", self.relevant_files)
        for path in (*self.relevant_files, *self.existing_tests):
            _relative_path(path)
        for name, values in (
            ("symbols", self.symbols),
            ("existing_tests", self.existing_tests),
            ("constraints", self.constraints),
            ("uncertainties", self.uncertainties),
            ("recommended_scope", self.recommended_scope),
        ):
            _bounded(name, values)


@dataclass(frozen=True)
class ProposedTest:
    name: str
    criterion: str
    setup: str
    assertion: str
    rationale: str

    def __post_init__(self) -> None:
        _nonempty((self.name, self.criterion, self.setup, self.assertion, self.rationale))


@dataclass(frozen=True)
class DesignedTestsResult:
    tests: tuple[ProposedTest, ...]
    unresolved_questions: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _version(self.schema_version)
        _bounded("tests", self.tests, maximum=50, require_one=True)
        _bounded("unresolved_questions", self.unresolved_questions)


@dataclass(frozen=True)
class FailureHypothesis:
    claim: str
    evidence: str
    confidence: float
    next_check: str

    def __post_init__(self) -> None:
        _nonempty((self.claim, self.evidence, self.next_check))
        _confidence(self.confidence)


@dataclass(frozen=True)
class FailureAnalysisResult:
    hypotheses: tuple[FailureHypothesis, ...]
    known_facts: tuple[str, ...]
    unresolved_questions: tuple[str, ...]
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _version(self.schema_version)
        _bounded("hypotheses", self.hypotheses, maximum=20, require_one=True)
        _bounded("known_facts", self.known_facts)
        _bounded("unresolved_questions", self.unresolved_questions)


@dataclass(frozen=True)
class ScoutFinding:
    severity: str
    criterion: str
    claim: str
    evidence: str
    confidence: float
    suggested_validation: str
    file: str | None = None
    line: int | None = None

    def __post_init__(self) -> None:
        if self.severity not in {"low", "medium", "high", "critical"}:
            raise ValueError("invalid finding severity")
        _nonempty((self.criterion, self.claim, self.evidence, self.suggested_validation))
        _confidence(self.confidence)
        if self.file is not None:
            _relative_path(self.file)
        if self.line is not None and self.line <= 0:
            raise ValueError("finding line must be positive")


@dataclass(frozen=True)
class ScoutResult:
    findings: tuple[ScoutFinding, ...]
    unresolved_questions: tuple[str, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        _version(self.schema_version)
        _bounded("findings", self.findings, maximum=50)
        _bounded("unresolved_questions", self.unresolved_questions)


def parse_result(role: AdvisoryRole, text: str) -> MapperResult | DesignedTestsResult | FailureAnalysisResult | ScoutResult:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("advisory result must be exactly one JSON object") from exc
    if not isinstance(payload, dict):
        raise ValueError("advisory result must be a JSON object")
    try:
        if role is AdvisoryRole.MAPPER:
            return MapperResult(**_tuples(payload, {"relevant_files", "symbols", "existing_tests", "constraints", "uncertainties", "recommended_scope"}))
        if role is AdvisoryRole.TEST_DESIGNER:
            data = dict(payload)
            data["tests"] = tuple(ProposedTest(**item) for item in data["tests"])
            data["unresolved_questions"] = tuple(data.get("unresolved_questions", ()))
            return DesignedTestsResult(**data)
        if role is AdvisoryRole.FAILURE_ANALYST:
            data = dict(payload)
            data["hypotheses"] = tuple(FailureHypothesis(**item) for item in data["hypotheses"])
            data["known_facts"] = tuple(data["known_facts"])
            data["unresolved_questions"] = tuple(data["unresolved_questions"])
            return FailureAnalysisResult(**data)
        if role is AdvisoryRole.SCOUT:
            data = dict(payload)
            data["findings"] = tuple(ScoutFinding(**item) for item in data["findings"])
            data["unresolved_questions"] = tuple(data.get("unresolved_questions", ()))
            return ScoutResult(**data)
    except (KeyError, TypeError) as exc:
        raise ValueError(f"invalid {role.value} result: {exc}") from exc
    raise ValueError(f"unsupported advisory role: {role}")


def _tuples(payload: dict[str, Any], fields: set[str]) -> dict[str, Any]:
    data = dict(payload)
    for field in fields:
        if field not in data:
            raise ValueError(f"missing field: {field}")
        data[field] = tuple(data[field])
    return data


def _version(version: int) -> None:
    if version != SCHEMA_VERSION:
        raise ValueError("unsupported advisory schema version")


def _bounded(name: str, values: tuple[Any, ...], *, maximum: int = MAX_ITEMS, require_one: bool = False) -> None:
    if require_one and not values:
        raise ValueError(f"{name} requires at least one item")
    if len(values) > maximum:
        raise ValueError(f"{name} exceeds {maximum} items")
    if len(set(values)) != len(values):
        raise ValueError(f"{name} contains duplicate items")
    for value in values:
        if isinstance(value, str) and not value.strip():
            raise ValueError(f"{name} contains an empty item")


def _relative_path(value: str) -> None:
    path = PurePosixPath(value)
    if not value.strip() or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"path must be repository-relative: {value}")


def _nonempty(values: tuple[str, ...]) -> None:
    if any(not value.strip() for value in values):
        raise ValueError("required text fields must be non-empty")


def _confidence(value: float) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0.0 <= value <= 1.0:
        raise ValueError("confidence must be between zero and one")
