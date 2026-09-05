"""DeepSeek prompt-ablation experiment suite v3.

This module extends role-suite v2 with:
- Challenge subset selection (28 cases including DeepSeek misses, clean controls, anchors)
- Four policy variants for DeepSeek: structured-v1, role-contract, role-verification, role-uncertainty
- Collision-safe artifact recording with identity validation
- Comprehensive aggregation metrics for promotion scoring

Usage:
    python3 -m qualification.role_suite.v3.runner verify
    python3 -m qualification.role_suite.v3.runner run --output /path --opencode /path --model deepseek-v4-flash --route default
"""
from __future__ import annotations

from .challenge_set import (
    get_challenge_cases,
    get_challenge_meta,
    get_ambiguous_case_ids,
    get_deepseek_miss_case_ids,
    AMBIGUOUS_CLEAN_CONTROLS,
    DEEPSEEK_MISSES_STRUCTURED_V2,
)
from .policies import (
    POLICIES,
    POLICY_CATEGORIES,
    ROLE_PROMPTS,
    get_policy_prompt,
    get_role_prompt,
    build_full_prompt,
)

__all__ = [
    "get_challenge_cases",
    "get_challenge_meta",
    "get_ambiguous_case_ids",
    "get_deepseek_miss_case_ids",
    "AMBIGUOUS_CLEAN_CONTROLS",
    "DEEPSEEK_MISSES_STRUCTURED_V2",
    "POLICIES",
    "POLICY_CATEGORIES",
    "ROLE_PROMPTS",
    "get_policy_prompt",
    "get_role_prompt",
    "build_full_prompt",
]
