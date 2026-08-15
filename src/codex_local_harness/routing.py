from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Sequence


class Disposition(str, Enum):
    LOCAL_DIRECT = "local-direct"
    LOCAL_WITH_CRITIC = "local-with-critic"
    CODEX_ONLY = "codex-only"


@dataclass(frozen=True)
class RoutingDecision:
    schema_version: int
    disposition: Disposition
    reasons: tuple[str, ...]
    critic_focus: tuple[str, ...]
    maximum_correction_cycles: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "disposition": self.disposition.value,
            "reasons": list(self.reasons),
            "critic_focus": list(self.critic_focus),
            "maximum_correction_cycles": self.maximum_correction_cycles,
        }


ESCALATION_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("authentication-or-authorization", re.compile(r"\b(auth(?:entication|orization)?|oauth|permission|rbac|access control)\b", re.I)),
    ("secrets-or-cryptography", re.compile(r"\b(secret|credential|token storage|cryptograph|encrypt|decrypt|signature)\b", re.I)),
    ("privacy", re.compile(r"\b(privacy|personal data|pii|gdpr|hipaa)\b", re.I)),
    ("migration-or-destructive", re.compile(r"\b(migrat\w*|schema change|drop table|delete data|destructive|data loss)\b", re.I)),
    ("public-api-compatibility", re.compile(r"\b(public api|backward compatibility|breaking change|deprecat)\b", re.I)),
    ("significant-dependency", re.compile(r"\b(new dependency|replace framework|major upgrade)\b", re.I)),
    ("conflicting-requirements", re.compile(r"\b(conflicting requirements?|simultaneously preserve .+ and change .+)\b", re.I)),
)

CRITIC_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("boundaries", re.compile(r"\b(boundar|edge case|empty|zero|negative|overflow|underflow|nan|infinity|finite)\b", re.I)),
    ("parsing-and-validation", re.compile(r"\b(parse|parser|validation|malformed|normalize|canonical)\b", re.I)),
    ("concurrency", re.compile(r"\b(concurr\w*|threads?|process(?:es)?|atomic|race|locks?|deadlock)\b", re.I)),
    ("time", re.compile(r"\b(ttl|expiry|expiration|clock|monotonic|timeout|retry)\b", re.I)),
    ("identity-and-equality", re.compile(r"\b(deduplic|equality|hashable|unhashable|ordering|first-seen)\b", re.I)),
    ("cleanup-and-failure", re.compile(r"\b(cleanup|temporary|crash-safe|partial write|rollback|exception contract)\b", re.I)),
)


def classify_issue(title: str, body: str, acceptance: Sequence[str]) -> RoutingDecision:
    text = "\n".join((title, body, *acceptance))
    escalation = [name for name, pattern in ESCALATION_RULES if pattern.search(text)]
    semantic_conflict = (
        re.search(r"case[- ]insensitive|ignore surrounding whitespace", text, re.I)
        and re.search(r"preserve .*(?:exact[- ]match|case[- ]sensitive)|exact[- ]match.*unchanged", text, re.I)
    )
    if semantic_conflict and "conflicting-requirements" not in escalation:
        escalation.append("conflicting-requirements")
    if escalation:
        return RoutingDecision(1, Disposition.CODEX_ONLY, tuple(escalation), (), 0)
    focus = [name for name, pattern in CRITIC_RULES if pattern.search(text)]
    if focus:
        return RoutingDecision(
            1,
            Disposition.LOCAL_WITH_CRITIC,
            ("deterministic-risk-rule",),
            tuple(focus),
            1,
        )
    return RoutingDecision(1, Disposition.LOCAL_DIRECT, ("no-risk-rule-triggered",), (), 0)


def write_decision(path: Path, decision: RoutingDecision) -> None:
    path.write_text(json.dumps(decision.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
