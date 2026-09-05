"""DeepSeek policy variants for prompt-ablation experiment.

Four policy variants based on role-specific contracts:
1. structured-v1: Existing 4-phase structured policy (baseline)
2. role-contract: Role-specific contract with clear instructions
3. role-verification: Role-contract plus omission/false-positive verification
4. role-uncertainty: Role-contract plus explicit uncertainty guidance
"""
from __future__ import annotations

from typing import Final


# Policy definitions with role-specific instructions
POLICIES: Final[dict[str, str]] = {
    # Baseline: Existing structured v1 policy (unchanged from v2)
    "structured-v1": (
        "Use four phases: inspect the supplied evidence, derive only supported conclusions, "
        "write result.json, then stop immediately. Do not perform optional investigation or repeat checks."
    ),
    # Variant 2: Role-specific contract with clear instructions
    "role-contract": (
        "You are a specialized analysis assistant with a defined role contract. "
        "Follow these steps strictly:\n"
        "1. READ: Analyze the evidence.json file completely\n"
        "2. ANALYZE: Extract only conclusions directly supported by the evidence\n"
        "3. WRITE: Output result.json with exactly one JSON object containing:\n"
        "   - 'answers': array of allowed labels that are materially supported\n"
        "   - 'evidence': array of brief supporting observations\n"
        "4. STOP: Write valid result.json and terminate immediately\n\n"
        "Contract rules:\n"
        "- Do not infer beyond what is explicitly stated or logically implied\n"
        "- Include every supported label; do not omit materially supported conclusions\n"
        "- Include no unsupported labels; avoid over-interpretation\n"
        "- Empty answers array is valid when no conclusions are supported"
    ),
    # Variant 3: Role-contract plus omission/false-positive verification
    "role-contract-plus-verification": (
        "You are a specialized analysis assistant with a defined role contract. "
        "Follow these steps strictly:\n"
        "1. READ: Analyze the evidence.json file completely\n"
        "2. ANALYZE: Extract conclusions directly supported by the evidence\n"
        "3. VERIFY: Perform explicit verification checks:\n"
        "   a) Omission check: For each allowed label, ask 'is this supported?'\n"
        "      Include it if the evidence provides material support\n"
        "   b) False-positive check: For each label you're considering, ask 'is this "
        "      supported, or am I over-interpreting?'\n"
        "4. WRITE: Output result.json with exactly one JSON object containing:\n"
        "   - 'answers': array of allowed labels verified as supported\n"
        "   - 'evidence': array of brief supporting observations from verification\n"
        "5. STOP: Write valid result.json and terminate immediately\n\n"
        "Contract rules:\n"
        "- Verification is mandatory; do not skip omission or false-positive checks\n"
        "- Do not infer beyond what passes both verification checks\n"
        "- When uncertain, prefer missing a signal over introducing a false positive\n"
        "- Empty answers array is valid when verification fails for all labels"
    ),
    # Variant 4: Role-contract plus explicit uncertainty guidance
    "role-contract-plus-uncertainty": (
        "You are a specialized analysis assistant with a defined role contract. "
        "Follow these steps strictly:\n"
        "1. READ: Analyze the evidence.json file completely\n"
        "2. ANALYZE: Extract conclusions directly supported by the evidence\n"
        "3. ASSESS: For each potential conclusion, explicitly assess uncertainty:\n"
        "   - High confidence: Evidence directly states or logically implies the conclusion\n"
        "   - Medium confidence: Evidence suggests but does not prove the conclusion\n"
        "   - Low confidence: Evidence is ambiguous or could support alternatives\n"
        "   Include only conclusions with high confidence\n"
        "4. WRITE: Output result.json with exactly one JSON object containing:\n"
        "   - 'answers': array of high-confidence allowed labels only\n"
        "   - 'evidence': array of brief supporting observations\n"
        "5. STOP: Write valid result.json and terminate immediately\n\n"
        "Contract rules:\n"
        "- Include only high-confidence conclusions; exclude medium/low confidence\n"
        "- Explicitly reject conclusions where ambiguity exists\n"
        "- When evidence is insufficient, use empty answers array\n"
        "- Never guess or assume missing context"
    ),
}


# Policy categories for grouping results
POLICY_CATEGORIES: Final[dict[str, str]] = {
    "structured-v1": "baseline",
    "role-contract": "role-specific",
    "role-contract-plus-verification": "role-specific+verification",
    "role-contract-plus-uncertainty": "role-specific+uncertainty",
}


# Role-specific prompts that should be prepended based on case category
ROLE_PROMPTS: Final[dict[str, str]] = {
    "reviewer": (
        "REVIEWER ROLE: You are a code reviewer. Your task is to identify defects, "
        "concurrency issues, missing tests, and security vulnerabilities in code patches."
    ),
    "investigator": (
        "INVESTIGATOR ROLE: You are a production incident investigator. Your task is to "
        "analyze logs, error messages, and system behavior to identify root causes."
    ),
    "test-strategy": (
        "TEST STRATEGY ROLE: You are a test automation specialist. Your task is to "
        "design comprehensive test strategies covering edge cases, regression, and integration."
    ),
    "non-coding": (
        "NON-CODING ANALYSIS ROLE: You are a systems analyst. Your task is to analyze "
        "documentation, specifications, and operational context to draw conclusions."
    ),
    "end-to-end": (
        "WORKFLOW ORCHESTRATION ROLE: You are a workflow coordinator. Your task is to "
        "analyze task states and propose appropriate workflow steps."
    ),
}


def get_policy_prompt(policy: str) -> str:
    """Return the base prompt for a policy."""
    if policy not in POLICIES:
        raise ValueError(f"unknown policy: {policy}")
    return POLICIES[policy]


def get_role_prompt(category: str) -> str:
    """Return the role-specific prompt for a case category."""
    if category not in ROLE_PROMPTS:
        # Fallback to general analysis role
        return ROLE_PROMPTS["non-coding"]
    return ROLE_PROMPTS[category]


def build_full_prompt(policy: str, category: str, evidence_path: str, result_path: str) -> str:
    """Build the complete prompt with role context and policy instructions."""
    role_prompt = get_role_prompt(category)
    policy_prompt = get_policy_prompt(policy)

    return f"""{role_prompt}

{policy_prompt}

Read {evidence_path}. This is a read-only analysis task: do not modify or create anything except {result_path}.

Return exactly one JSON object in {result_path} with this schema:
{{"answers": ["ALLOWED_LABEL", "..."], "evidence": ["brief supporting observation", "..."]}}

Use only labels listed in evidence.json. Include every materially supported label and no unsupported label. An empty answers list is valid. Stop immediately after writing valid result.json."""
