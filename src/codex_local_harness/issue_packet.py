"""Versioned structured issue packet for paired-model cases.

Defines a deterministic, versioned issue packet structure that contains:
- objective: The primary goal
- context: Background and constraints
- scope: What is in-scope
- acceptance_criteria: Testable success criteria
- constraints_exclusions: Explicit out-of-scope items
- required_verification: How correctness is determined
- durable_deliverable: The expected output artifact
- escalation_conditions: When to escalate to Codex

The packet is model-independent and identical for the same case/policy comparison.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .coding_contracts import CONTRACT_TEMPLATES


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class IssuePacket:
    """Versioned structured issue packet with deterministic identity."""
    schema_version: int
    packet_id: str
    case_id: str
    category: str
    objective: str
    context: dict[str, Any]
    scope: list[str]
    acceptance_criteria: list[str]
    constraints_exclusions: list[str]
    required_verification: dict[str, Any]
    durable_deliverable: str
    escalation_conditions: list[str]
    created_at: str
    identity_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema version: {self.schema_version}")
        if not self.packet_id or not self.packet_id.strip():
            raise ValueError("packet_id must be non-empty")
        if not self.case_id or not self.case_id.strip():
            raise ValueError("case_id must be non-empty")
        if not self.category or not self.category.strip():
            raise ValueError("category must be non-empty")
        if not self.objective or not self.objective.strip():
            raise ValueError("objective must be non-empty")

    @classmethod
    def compute_hash(cls, content: dict[str, Any]) -> str:
        """Compute deterministic SHA-256 hash for packet content."""
        encoded = json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def create(
        cls,
        case_id: str,
        category: str,
        objective: str,
        context: dict[str, Any],
        scope: list[str],
        acceptance_criteria: list[str],
        constraints_exclusions: list[str],
        required_verification: dict[str, Any],
        durable_deliverable: str,
        escalation_conditions: list[str],
    ) -> IssuePacket:
        """Create an issue packet with computed identity hash."""
        content = {
            "case_id": case_id,
            "category": category,
            "objective": objective,
            "context": context,
            "scope": sorted(scope),
            "acceptance_criteria": sorted(acceptance_criteria),
            "constraints_exclusions": sorted(constraints_exclusions),
            "required_verification": required_verification,
            "durable_deliverable": durable_deliverable,
            "escalation_conditions": sorted(escalation_conditions),
        }
        identity_hash = cls.compute_hash(content)

        return cls(
            schema_version=SCHEMA_VERSION,
            packet_id=f"packet-{case_id}",
            case_id=case_id,
            category=category,
            objective=objective,
            context=context,
            scope=scope,
            acceptance_criteria=acceptance_criteria,
            constraints_exclusions=constraints_exclusions,
            required_verification=required_verification,
            durable_deliverable=durable_deliverable,
            escalation_conditions=escalation_conditions,
            created_at=datetime.now(timezone.utc).isoformat(),
            identity_hash=identity_hash,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return as dictionary (excluding created_at for identity consistency)."""
        data = asdict(self)
        # Remove created_at from identity-critical fields
        identity_data = {k: v for k, v in data.items() if k != "created_at"}
        return identity_data

    def render_markdown(self) -> str:
        """Render packet as Markdown for human readability."""
        lines = [
            f"# Issue Packet: {self.case_id}",
            "",
            f"**Packet ID:** {self.packet_id}",
            f"**Category:** {self.category}",
            "",
            "## Objective",
            "",
            self.objective,
            "",
            "## Context",
            "",
        ]
        for key, value in self.context.items():
            lines.append(f"- **{key}:** {value}")
        lines.extend(["", "## Scope", ""])
        for item in self.scope:
            lines.append(f"- {item}")
        lines.extend(["", "## Acceptance Criteria", ""])
        for item in self.acceptance_criteria:
            lines.append(f"- {item}")
        lines.extend(["", "## Constraints / Exclusions", ""])
        for item in self.constraints_exclusions:
            lines.append(f"- {item}")
        lines.extend(["", "## Required Verification", ""])
        lines.append(f"```json\n{json.dumps(self.required_verification, indent=2)}\n```")
        lines.extend(["", "## Durable Deliverable", "", self.durable_deliverable, ""])
        lines.extend(["## Escalation Conditions", ""])
        for item in self.escalation_conditions:
            lines.append(f"- {item}")
        return "\n".join(lines)


def build_issue_packet_for_case(
    case_id: str,
    category: str,
    task_content: str,
    check_command: list[str],
) -> IssuePacket:
    """Build an issue packet from paired-model case data.

    This function extracts the objective and context from the task content,
    and builds acceptance criteria based on the check command structure.
    """
    # Extract key information from task content
    lines = task_content.strip().split("\n")
    objective = lines[0].strip() if lines else f"Address case {case_id}"

    # Determine scope based on category
    scope_map = {
        "investigation": ["Root cause analysis", "Behavioral documentation"],
        "focused_fix": ["Bug fix", "Minimal change", "Preserve existing behavior"],
        "tests": ["Test coverage", "Edge cases", "Regression prevention"],
        "ui": ["CLI output", "User-facing changes"],
        "refactoring": ["Code structure", "Maintain behavior", "Improve organization"],
        "diagnosis": ["Logging", "Telemetry", "Debugging support"],
        "hygiene": ["Formatting", "Style compliance", "Linting"],
        "review": ["Code review", "Security", "Concurrency"],
        "concurrency": ["Thread safety", "Race condition prevention", "Lock usage"],
        "cleanup": ["Atomic operations", "Error handling", "Cleanup logic"],
    }
    scope = scope_map.get(category, ["General coding task"])

    # Build acceptance criteria from check command
    acceptance_criteria = []
    if "oracle.py" in str(check_command):
        if "investigation" in case_id:
            acceptance_criteria.extend([
                "Findings document must include 'identity' and 'normalize' keywords",
                "Documentation must explain root cause and normalization approach",
            ])
        elif "focused-fix" in case_id:
            acceptance_criteria.extend([
                "normalize('') returns ''",
                "normalize('  HELLO ') returns 'hello'",
                "No 'is' operators in ast.parse() tree",
            ])
        elif "test-authoring" in case_id:
            acceptance_criteria.extend([
                "Test file must include 'parse_port' function",
                "unittest -q must pass",
            ])
        elif "ui" in case_id:
            acceptance_criteria.extend([
                "render_message('<script>alert(\"x\")</script>') escapes HTML",
            ])
        elif "refactor" in case_id:
            acceptance_criteria.extend([
                "metric_total([1,2,3]) returns 6",
                "metric_total([]) returns 0",
                "Source contains 'sum(values)'",
            ])
        elif "diagnosis" in case_id:
            acceptance_criteria.extend([
                "acquire_lock(lock) returns True for empty lock file",
            ])
        elif "hygiene" in case_id:
            acceptance_criteria.extend([
                "add_header('one') and add_header('two') return distinct headers",
            ])
        elif "review" in case_id:
            acceptance_criteria.extend([
                "REVIEW.json must include 'path', 'travers', 'safe_join'",
            ])
        elif "concurrency" in case_id:
            acceptance_criteria.extend([
                "Counter increments 8000 times across 8 threads",
                "Counter has _lock attribute",
            ])
        elif "cleanup" in case_id:
            acceptance_criteria.extend([
                "write_then_fail() must clean up partial.txt on failure",
            ])

    # Build constraints based on category
    constraints = {
        "category": category,
        "timeout_seconds": 180,
        "workspace_immutable": True,
        "oracle_separated": True,
    }
    if category == "concurrency":
        constraints["thread_safety_required"] = True

    # Required verification
    required_verification = {
        "command": check_command,
        "oracle_separated": True,
        "workspace_immutable": True,
    }

    # Durable deliverable
    deliverable_map = {
        "investigation": "FINDINGS.md with root cause and normalization",
        "focused-fix": "Modified app.py with bug fix",
        "tests": "test_app.py with unit tests",
        "ui": "Modified app.py with UI changes",
        "refactor": "Refactored app.py with sum(values)",
        "diagnosis": "Modified app.py with diagnostic logging",
        "hygiene": "Modified app.py with hygiene improvements",
        "review": "REVIEW.json with findings",
        "concurrency": "Modified app.py with thread-safe Counter",
        "cleanup": "Modified app.py with atomic cleanup",
    }
    durable_deliverable = deliverable_map.get(category, f"Result for {case_id}")

    # Escalation conditions
    escalation = [
        "Unexpected file modifications",
        "Oracle access from worker",
        "Timeout exceeded without partial results",
        "Permission errors",
    ]

    return IssuePacket.create(
        case_id=case_id,
        category=category,
        objective=objective,
        context=constraints,
        scope=scope,
        acceptance_criteria=acceptance_criteria,
        constraints_exclusions=["Real GitHub mutations", "External API calls", "Manual intervention"],
        required_verification=required_verification,
        durable_deliverable=durable_deliverable,
        escalation_conditions=escalation,
    )


def load_manifest_packets(manifest_path: Path) -> dict[str, IssuePacket]:
    """Load issue packets from a paired-model manifest."""
    import json

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("unsupported manifest schema version")

    packets = {}
    for case in data.get("cases", []):
        packet = build_issue_packet_for_case(
            case_id=case["id"],
            category=case["category"],
            task_content=(manifest_path.parent / case["task"]).read_text(encoding="utf-8"),
            check_command=case["check"],
        )
        packets[case["id"]] = packet

    return packets


def identity_hash_from_packet(packet: IssuePacket, policy_variant: str = "baseline") -> str:
    """Compute identity hash binding packet, policy variant, and model context.

    This ensures that different policy variants produce different identity hashes
    even for the same issue packet, enabling fair policy comparison.
    """
    content = {
        "packet_id": packet.packet_id,
        "case_id": packet.case_id,
        "category": packet.category,
        "objective": packet.objective,
        "policy_variant": policy_variant,
    }
    return IssuePacket.compute_hash(content)


# Re-export for backward compatibility
from .coding_contracts import (
    bind_contract_to_case,
    build_full_prompt,
    compute_contract_identity,
    ContractInstance,
)
