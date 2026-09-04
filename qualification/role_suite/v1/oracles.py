"""External deterministic oracles for role-suite v1 validation."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class OracleCheck:
    """A single oracle check specification."""
    check_id: str
    suite: str
    category: str
    case_id: str
    check_type: str  # defect_count, analysis_complete, strategy_complete, workflow_complete, etc.
    expected_value: str | int | float | bool | None
    constraint: str  # =, >=, <=, !=, in, regex
    required_fields: tuple[str, ...]
    error_message: str = ""


@dataclass(frozen=True)
class OracleResult:
    """Result of an oracle check."""
    check_id: str
    passed: bool
    actual_value: Any
    message: str
    timestamp: str


def parse_oracle_check(oracle_spec: str, suite: str, category: str, case_id: str) -> OracleCheck:
    """Parse an oracle check from string specification."""
    check_id = f"{suite}:{category}:{case_id}"

    # Parse common patterns
    # Pattern: "defect_count:1" or "defect_count:>=2"
    # Pattern: "analysis_complete:true"
    # Pattern: "workflow_complete:true"

    if ":" not in oracle_spec:
        raise ValueError(f"Invalid oracle spec: {oracle_spec}")

    check_type, constraint = oracle_spec.split(":", 1)

    # Parse constraint and expected value
    if constraint.startswith(">="):
        constraint_op = ">="
        expected = constraint[2:].strip()
    elif constraint.startswith("<="):
        constraint_op = "<="
        expected = constraint[2:].strip()
    elif constraint.startswith("!="):
        constraint_op = "!="
        expected = constraint[2:].strip()
    elif constraint.startswith(">"):
        constraint_op = ">"
        expected = constraint[1:].strip()
    elif constraint.startswith("<"):
        constraint_op = "<"
        expected = constraint[1:].strip()
    elif constraint.startswith("in "):
        constraint_op = "in"
        expected = constraint[3:].strip()
    elif constraint.startswith("regex "):
        constraint_op = "regex"
        expected = constraint[6:].strip()
    else:
        constraint_op = "="
        expected = constraint

    # Convert expected value type
    expected_value: Any = expected
    if expected.lower() in ("true", "false"):
        expected_value = expected.lower() == "true"
    elif expected.isdigit():
        expected_value = int(expected)
    elif re.match(r"^\d+\.\d+$", expected):
        expected_value = float(expected)

    return OracleCheck(
        check_id=check_id,
        suite=suite,
        category=category,
        case_id=case_id,
        check_type=check_type,
        expected_value=expected_value,
        constraint=constraint_op,
        required_fields=(),
        error_message="",
    )


def run_oracle_check(
    oracle: OracleCheck,
    output: dict[str, Any] | None = None,
    output_path: Path | None = None,
) -> OracleResult:
    """Run an oracle check against output."""
    check_id = oracle.check_id

    # Load output if from file
    actual_value: Any = None
    if output is None and output_path is not None:
        try:
            with output_path.open("r", encoding="utf-8") as f:
                output = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return OracleResult(
                check_id=check_id,
                passed=False,
                actual_value=None,
                message="Output file not found or invalid JSON",
                timestamp="",
            )

    # Execute check based on type
    if oracle.check_type == "defect_count":
        if output is None:
            return OracleResult(
                check_id=check_id,
                passed=False,
                actual_value=None,
                message="No output provided for defect_count check",
                timestamp="",
            )
        # Count defects in findings array
        findings = output.get("findings", [])
        actual_value = len(findings)

        if oracle.constraint == "=":
            passed = actual_value == oracle.expected_value
        elif oracle.constraint == ">=":
            passed = actual_value >= oracle.expected_value
        elif oracle.constraint == "<=":
            passed = actual_value <= oracle.expected_value
        else:
            passed = False

    elif oracle.check_type == "analysis_complete":
        if output is None:
            return OracleResult(
                check_id=check_id,
                passed=False,
                actual_value=None,
                message="No output provided for analysis_complete check",
                timestamp="",
            )
        actual_value = "analysis" in output or "findings" in output
        passed = actual_value is True

    elif oracle.check_type == "strategy_complete":
        if output is None:
            return OracleResult(
                check_id=check_id,
                passed=False,
                actual_value=None,
                message="No output provided for strategy_complete check",
                timestamp="",
            )
        actual_value = "tests" in output or "strategy" in output
        passed = actual_value is True

    elif oracle.check_type == "workflow_complete":
        if output is None:
            return OracleResult(
                check_id=check_id,
                passed=False,
                actual_value=None,
                message="No output provided for workflow_complete check",
                timestamp="",
            )
        actual_value = "workflow" in output or "phases" in output
        passed = actual_value is True

    elif oracle.check_type == "json_schema":
        if output is None:
            return OracleResult(
                check_id=check_id,
                passed=False,
                actual_value=None,
                message="No output provided for json_schema check",
                timestamp="",
            )
        # Check for required fields
        actual_value = all(field in output for field in oracle.required_fields)
        passed = actual_value is True

    elif oracle.check_type == "terminal_format":
        if output is None:
            return OracleResult(
                check_id=check_id,
                passed=False,
                actual_value=None,
                message="No output provided for terminal_format check",
                timestamp="",
            )
        # Check if output is valid JSON
        try:
            actual_value = isinstance(output, dict)
            passed = actual_value is True
        except:
            passed = False

    else:
        return OracleResult(
            check_id=check_id,
            passed=False,
            actual_value=None,
            message=f"Unknown check type: {oracle.check_type}",
            timestamp="",
        )

    return OracleResult(
        check_id=check_id,
        passed=passed,
        actual_value=actual_value,
        message="Check passed" if passed else f"Check failed: expected {oracle.constraint} {oracle.expected_value}",
        timestamp="",
    )


def validate_output_schema(
    output: dict[str, Any],
    role: str,
    required_fields: tuple[str, ...] = (),
) -> OracleResult:
    """Validate output schema for a given role."""
    check_id = f"schema:{role}"

    # Common required fields
    common_required = ("schema_version", "run_id", "model")

    # Role-specific required fields
    role_required: dict[str, tuple[str, ...]] = {
        "reviewer": ("verdict", "findings", "confidence"),
        "investigator": ("analysis", "findings"),
        "test-strategy": ("tests", "coverage"),
        "non-coding": ("review", "findings"),
    }

    all_required = common_required + role_required.get(role, ())

    missing = [f for f in all_required if f not in output]

    if required_fields:
        missing += [f for f in required_fields if f not in output]

    passed = len(missing) == 0

    return OracleResult(
        check_id=check_id,
        passed=passed,
        actual_value=missing if missing else "all fields present",
        message="Schema valid" if passed else f"Missing fields: {', '.join(missing)}",
        timestamp="",
    )


def check_read_only_enforcement(
    events: list[dict[str, Any]],
    allowed_paths: tuple[str, ...] = (),
) -> OracleResult:
    """Check that worker only used read-only operations where specified."""
    check_id = "read-only:enforcement"

    # Look for file write operations
    write_patterns = [
        "write_file",
        "edit_file",
        "create_file",
        "delete_file",
        "replace_file",
    ]

    write_ops = []
    for event in events:
        if event.get("type") == "tool_use":
            tool = event.get("part", {}).get("tool", "")
            if any(pattern in tool.lower() for pattern in write_patterns):
                write_ops.append(tool)

    passed = len(write_ops) == 0

    return OracleResult(
        check_id=check_id,
        passed=passed,
        actual_value=write_ops,
        message="No write operations detected" if passed else f"Write operations detected: {', '.join(write_ops)}",
        timestamp="",
    )


def check_token_budget(
    usage: dict[str, int] | None,
    budget: int,
) -> OracleResult:
    """Check that token usage is within budget."""
    if usage is None:
        return OracleResult(
            check_id="token:budget",
            passed=True,
            actual_value=None,
            message="No usage data available",
            timestamp="",
        )

    total = usage.get("local_total", 0) + usage.get("frontier_total", 0)

    passed = total <= budget

    return OracleResult(
        check_id="token:budget",
        passed=passed,
        actual_value=total,
        message="Within budget" if passed else f"Exceeded budget: {total} > {budget}",
        timestamp="",
    )


def check_timeout(
    elapsed_seconds: float,
    timeout_seconds: float,
) -> OracleResult:
    """Check that execution completed within timeout."""
    passed = elapsed_seconds <= timeout_seconds

    return OracleResult(
        check_id="timeout:compliance",
        passed=passed,
        actual_value=elapsed_seconds,
        message="Within timeout" if passed else f"Timeout exceeded: {elapsed_seconds} > {timeout_seconds}",
        timestamp="",
    )


def check_identity_match(
    expected_hash: str,
    actual_hash: str,
) -> OracleResult:
    """Check that case identity hashes match."""
    passed = expected_hash == actual_hash

    return OracleResult(
        check_id=f"identity:{expected_hash[:8]}",
        passed=passed,
        actual_value=actual_hash,
        message="Identity match" if passed else f"Identity mismatch: {actual_hash} != {expected_hash}",
        timestamp="",
    )


def check_false_positive(
    findings: list[dict[str, Any]],
    known_truth: list[str],
) -> OracleResult:
    """Check for false positive findings (reported bug that doesn't exist)."""
    check_id = "false-positive:detection"

    false_positives = []
    for finding in findings:
        claim = finding.get("claim", "")
        evidence = finding.get("evidence", "")
        for truth in known_truth:
            # Check if finding contradicts known truth
            if truth.lower() in claim.lower() or truth.lower() in evidence.lower():
                false_positives.append(finding)

    passed = len(false_positives) == 0

    return OracleResult(
        check_id=check_id,
        passed=passed,
        actual_value=len(false_positives),
        message="No false positives" if passed else f"Found {len(false_positives)} false positives",
        timestamp="",
    )


def check_clean_review(
    findings: list[dict[str, Any]],
    should_have_findings: bool,
) -> OracleResult:
    """Check that clean patches receive no findings."""
    check_id = "clean-review:penalty"

    if not should_have_findings and len(findings) > 0:
        # Clean patch got findings - false positive penalty
        passed = False
        message = f"Clean patch incorrectly flagged with {len(findings)} findings"
    elif should_have_findings and len(findings) == 0:
        # Patch with defects got no findings - missed detection
        passed = False
        message = "Defects not detected"
    else:
        passed = True
        message = "Review correct"

    return OracleResult(
        check_id=check_id,
        passed=passed,
        actual_value=len(findings),
        message=message,
        timestamp="",
    )
