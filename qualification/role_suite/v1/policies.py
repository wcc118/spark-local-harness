"""Policy variants for role-suite v1."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PolicyConfig:
    """Configuration for a policy variant."""
    policy_id: str
    name: str
    description: str
    stop_on_first_failure: bool
    phased_execution: bool
    checkpoint_interval: int | None
    timeout_override: int | None
    read_only: bool
    allow_tool_call: tuple[str, ...]
    deny_tool_call: tuple[str, ...]
    extra_instructions: tuple[str, ...]


# Policy definitions
POLICY_DEFINITIONS: dict[str, PolicyConfig] = {
    "baseline": PolicyConfig(
        policy_id="baseline",
        name="Baseline Evaluation",
        description="Standard evaluation without modifications",
        stop_on_first_failure=False,
        phased_execution=False,
        checkpoint_interval=None,
        timeout_override=None,
        read_only=False,
        allow_tool_call=(),
        deny_tool_call=(),
        extra_instructions=(),
    ),
    "explicit-stop": PolicyConfig(
        policy_id="explicit-stop",
        name="Explicit Stop",
        description="Terminate after first failure for faster iteration",
        stop_on_first_failure=True,
        phased_execution=False,
        checkpoint_interval=None,
        timeout_override=None,
        read_only=False,
        allow_tool_call=(),
        deny_tool_call=(),
        extra_instructions=(
            "STOP immediately if any check fails.",
            "Do not attempt recovery or alternative approaches.",
        ),
    ),
    "structured/phased": PolicyConfig(
        policy_id="structured/phased",
        name="Structured Phased",
        description="Multi-phase evaluation with checkpointing",
        stop_on_first_failure=False,
        phased_execution=True,
        checkpoint_interval=3,
        timeout_override=None,
        read_only=False,
        allow_tool_call=(),
        deny_tool_call=(),
        extra_instructions=(
            "Execute in distinct phases: analysis, planning, execution, verification.",
            "Checkpoint after each phase before proceeding.",
            "If any phase fails, report findings and stop.",
        ),
    ),
}


def get_policy_config(policy_id: str) -> PolicyConfig:
    """Get policy configuration by ID."""
    if policy_id not in POLICY_DEFINITIONS:
        raise ValueError(f"Unknown policy: {policy_id}")
    return POLICY_DEFINITIONS[policy_id]


def build_prompt_prefix(policy: PolicyConfig) -> str:
    """Build the policy-specific prompt prefix."""
    lines = [
        f"## Policy: {policy.name}",
        "",
        policy.description,
        "",
    ]

    if policy.stop_on_first_failure:
        lines.extend([
            "**CRITICAL: Stop on First Failure**",
            "If any validation check fails, STOP immediately.",
            "Do not attempt recovery or alternative approaches.",
            "Report the failure and terminate.",
            "",
        ])

    if policy.phased_execution:
        lines.extend([
            "**Structured Phases**",
            "Execute in these distinct phases:",
            "1. **Analysis**: Examine the provided context and requirements.",
            "2. **Planning**: Formulate a plan based on analysis.",
            "3. **Execution**: Carry out the plan.",
            "4. **Verification**: Check results against requirements.",
            "",
            "Checkpoint after each phase.",
            "",
        ])

    if policy.timeout_override:
        lines.extend([
            f"**Timeout Override**: {policy.timeout_override} seconds",
            "",
        ])

    if policy.read_only:
        lines.extend([
            "**READ-ONLY MODE**",
            "You must NOT write, edit, or delete any files.",
            "You must NOT execute commands that modify state.",
            "Provide analysis and recommendations only.",
            "",
        ])

    if policy.deny_tool_call:
        lines.extend([
            "**DENIED TOOLS**",
            f"You must NOT use these tools: {', '.join(policy.deny_tool_call)}",
            "",
        ])

    if policy.extra_instructions:
        lines.extend([
            "**Additional Instructions**",
            "",
        ])
        for instruction in policy.extra_instructions:
            lines.append(f"- {instruction}")
        lines.append("")

    return "\n".join(lines)


def get_policy_instructions(policy_id: str) -> str:
    """Get policy-specific instructions for workers."""
    policy = get_policy_config(policy_id)
    return build_prompt_prefix(policy)


def apply_policy_to_context(context: str, policy_id: str) -> str:
    """Apply policy instructions to a context string."""
    policy = get_policy_config(policy_id)
    prefix = build_prompt_prefix(policy)
    return f"{prefix}\n{context}"


def should_stop_on_failure(policy_id: str) -> bool:
    """Check if policy should stop on first failure."""
    policy = get_policy_config(policy_id)
    return policy.stop_on_first_failure


def is_phased(policy_id: str) -> bool:
    """Check if policy uses phased execution."""
    policy = get_policy_config(policy_id)
    return policy.phased_execution


def get_checkpoint_interval(policy_id: str) -> int | None:
    """Get checkpoint interval for phased execution."""
    policy = get_policy_config(policy_id)
    return policy.checkpoint_interval


def is_read_only(policy_id: str) -> bool:
    """Check if policy enforces read-only mode."""
    policy = get_policy_config(policy_id)
    return policy.read_only
