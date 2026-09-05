"""Named, versioned coding contracts for paired-model execution.

 Defines reusable contract templates that bind:
 - Full effective prompt (instruction prefix + base prompt)
 - Model-independent case inputs
 - Permission profile
 - Evaluator/check logic

Contracts are distinct from issue packets but work together to ensure
deterministic, comparable benchmark runs.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ContractTemplate:
    """Template for a reusable coding contract."""
    name: str
    version: str
    description: str
    instruction_prefix: str
    permission_profile: str
    evaluator_signature: str  # e.g., "check(case_id, workspace) -> bool"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# Built-in contract templates
CONTRACT_TEMPLATES: dict[str, ContractTemplate] = {
    "baseline-production": ContractTemplate(
        name="baseline-production",
        version="v1.0.0",
        description="Standard production contract: minimal instructions, no explicit phases",
        instruction_prefix="# Production coding task. Respond with only the required code.",
        permission_profile="benchmark-coder-v1",
        evaluator_signature="check(case_id, workspace) -> bool",
    ),
    "structured-inspect-implement-verify": ContractTemplate(
        name="structured-inspect-implement-verify",
        version="v1.0.0",
        description="4-phase structured contract: Inspect, Implement, Verify, Deliver",
        instruction_prefix=(
            "# Structured coding contract\n"
            "\n"
            "Follow these four bounded phases exactly:\n"
            "\n"
            "1. INSPECT: Analyze the current state, identify required changes\n"
            "2. IMPLEMENT: Make minimal, targeted changes\n"
            "3. VERIFY: Run checks to confirm correctness\n"
            "4. DELIVER: Write final output to the expected location\n"
            "\n"
            "Do not deviate from this structure."
        ),
        permission_profile="benchmark-coder-v1",
        evaluator_signature="check(case_id, workspace) -> bool",
    ),
    "acceptance-criteria-test-oriented": ContractTemplate(
        name="acceptance-criteria-test-oriented",
        version="v1.0.0",
        description="Test-driven contract: acceptance criteria come first, then implementation",
        instruction_prefix=(
            "# Acceptance-criteria-driven contract\n"
            "\n"
            "1. Read the acceptance criteria below\n"
            "2. Write tests that verify each criterion\n"
            "3. Implement code to make tests pass\n"
            "4. Run all tests to confirm completion\n"
            "\n"
            "Acceptance criteria are binding - implement only what is specified."
        ),
        permission_profile="benchmark-coder-v1",
        evaluator_signature="check(case_id, workspace) -> bool",
    ),
    "verification-plus-uncertainty-escalation": ContractTemplate(
        name="verification-plus-uncertainty-escalation",
        version="v1.0.0",
        description="Verification-focused contract with explicit uncertainty assessment",
        instruction_prefix=(
            "# Verification-plus contract\n"
            "\n"
            "1. Analyze the task and identify potential edge cases\n"
            "2. Implement the solution\n"
            "3. Assess your confidence (HIGH/MEDIUM/LOW) in correctness\n"
            "4. If confidence < HIGH, escalate to reviewer for verification\n"
            "\n"
            "Explicit uncertainty assessment is required."
        ),
        permission_profile="benchmark-coder-v1",
        evaluator_signature="check(case_id, workspace) -> bool",
    ),
}


@dataclass(frozen=True)
class ContractInstance:
    """Instance of a contract bound to specific inputs."""
    template_name: str
    template_version: str
    case_id: str
    category: str
    full_effective_prompt: str
    instruction_prefix: str
    case_inputs_hash: str  # SHA-256 of case-specific inputs
    permission_profile: str
    evaluator_signature: str
    identity_hash: str
    created_at: str

    def __post_init__(self) -> None:
        if not self.template_name:
            raise ValueError("template_name must be non-empty")
        if not self.case_id:
            raise ValueError("case_id must be non-empty")

    @classmethod
    def create(
        cls,
        template: ContractTemplate,
        case_id: str,
        category: str,
        base_prompt: str,
        case_inputs_hash: str,
    ) -> ContractInstance:
        """Create a contract instance bound to specific inputs."""
        full_prompt = template.instruction_prefix + "\n\n" + base_prompt

        content = {
            "template_name": template.name,
            "template_version": template.version,
            "case_id": case_id,
            "category": category,
            "full_effective_prompt": full_prompt,
            "case_inputs_hash": case_inputs_hash,
            "permission_profile": template.permission_profile,
        }
        identity_hash = hashlib.sha256(
            json.dumps(content, sort_keys=True).encode()
        ).hexdigest()

        return cls(
            template_name=template.name,
            template_version=template.version,
            case_id=case_id,
            category=category,
            full_effective_prompt=full_prompt,
            instruction_prefix=template.instruction_prefix,
            case_inputs_hash=case_inputs_hash,
            permission_profile=template.permission_profile,
            evaluator_signature=template.evaluator_signature,
            identity_hash=identity_hash,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def verify_identity_match(self, identity_hash: str) -> bool:
        """Verify that this instance's identity matches the expected hash."""
        return self.identity_hash == identity_hash


def bind_contract_to_case(
    template_name: str,
    case_id: str,
    category: str,
    base_prompt: str,
    case_inputs: dict[str, Any],
) -> ContractInstance:
    """Bind a contract template to specific case inputs.

    Args:
        template_name: Name of the contract template
        case_id: Case identifier
        category: Case category
        base_prompt: Base task prompt without instruction prefix
        case_inputs: Case-specific inputs (context, expected, etc.)

    Returns:
        ContractInstance with full effective prompt and identity hash

    Raises:
        ValueError: If template_name is unknown
    """
    if template_name not in CONTRACT_TEMPLATES:
        raise ValueError(f"unknown contract template: {template_name}")

    template = CONTRACT_TEMPLATES[template_name]

    # Compute hash of case inputs (without oracle data)
    inputs_for_hash = {
        k: v for k, v in case_inputs.items()
        if k not in ("expected", "forbidden", "oracle")
    }
    case_inputs_hash = hashlib.sha256(
        json.dumps(inputs_for_hash, sort_keys=True).encode()
    ).hexdigest()

    return ContractInstance.create(
        template=template,
        case_id=case_id,
        category=category,
        base_prompt=base_prompt,
        case_inputs_hash=case_inputs_hash,
    )


def build_full_prompt(template_name: str, base_prompt: str) -> str:
    """Build the full effective prompt from a contract template.

    This is model-independent and identical for the same case/policy comparison.
    """
    if template_name not in CONTRACT_TEMPLATES:
        raise ValueError(f"unknown contract template: {template_name}")

    template = CONTRACT_TEMPLATES[template_name]
    return template.instruction_prefix + "\n\n" + base_prompt


def compute_contract_identity(
    template_name: str,
    case_id: str,
    category: str,
    base_prompt: str,
    case_inputs_hash: str,
) -> str:
    """Compute the identity hash for a contract instance without creating it.

    This allows identity verification before full instantiation.
    """
    template = CONTRACT_TEMPLATES.get(template_name)
    if template is None:
        raise ValueError(f"unknown contract template: {template_name}")

    full_prompt = template.instruction_prefix + "\n\n" + base_prompt

    content = {
        "template_name": template.name,
        "template_version": template.version,
        "case_id": case_id,
        "category": category,
        "full_effective_prompt": full_prompt,
        "case_inputs_hash": case_inputs_hash,
        "permission_profile": template.permission_profile,
    }
    return hashlib.sha256(
        json.dumps(content, sort_keys=True).encode()
    ).hexdigest()
