from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from .diagnostics import _safe_atom


@dataclass(frozen=True)
class CompactionQualification:
    attempt_id: str
    profile: str
    primary_input_tokens: int
    primary_cached_input_tokens: int
    compaction_pass_input_tokens: int
    compaction_pass_cached_input_tokens: int
    resume_input_tokens: int
    resume_cached_input_tokens: int
    tool_loops: int
    wall_seconds: float
    state_retained: bool
    task_completed: bool
    task_outcome: str

    def __post_init__(self) -> None:
        _safe_atom(self.attempt_id, "attempt_id")
        _safe_atom(self.profile, "profile")
        _safe_atom(self.task_outcome, "task_outcome")
        for name in (
            "primary_input_tokens", "primary_cached_input_tokens",
            "compaction_pass_input_tokens", "compaction_pass_cached_input_tokens",
            "resume_input_tokens", "resume_cached_input_tokens", "tool_loops",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.primary_cached_input_tokens > self.primary_input_tokens:
            raise ValueError("cached primary input cannot exceed primary input")
        if self.compaction_pass_cached_input_tokens > self.compaction_pass_input_tokens:
            raise ValueError("cached compaction input cannot exceed compaction input")
        if self.resume_cached_input_tokens > self.resume_input_tokens:
            raise ValueError("cached resume input cannot exceed resume input")
        if self.wall_seconds < 0:
            raise ValueError("wall_seconds must be non-negative")

    @property
    def net_input_tokens(self) -> int:
        return (
            self.primary_input_tokens
            + self.compaction_pass_input_tokens
            + self.resume_input_tokens
        )

    @property
    def net_cached_input_tokens(self) -> int:
        return (
            self.primary_cached_input_tokens
            + self.compaction_pass_cached_input_tokens
            + self.resume_cached_input_tokens
        )

    @property
    def net_uncached_input_tokens(self) -> int:
        return self.net_input_tokens - self.net_cached_input_tokens

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "net_input_tokens": self.net_input_tokens,
            "net_cached_input_tokens": self.net_cached_input_tokens,
            "net_uncached_input_tokens": self.net_uncached_input_tokens,
        }


@dataclass(frozen=True)
class QualificationDecision:
    promote: bool
    reasons: tuple[str, ...]
    input_improvement: float | None


def evaluate_qualification(
    baseline: CompactionQualification,
    candidate: CompactionQualification,
    *, minimum_input_improvement: float = 0.15,
) -> QualificationDecision:
    if not 0 < minimum_input_improvement < 1:
        raise ValueError("minimum_input_improvement must be between zero and one")
    reasons: list[str] = []
    improvement = None
    if baseline.net_input_tokens > 0:
        improvement = 1 - candidate.net_input_tokens / baseline.net_input_tokens
        if improvement < minimum_input_improvement:
            reasons.append("input-improvement-below-threshold")
    else:
        reasons.append("baseline-input-unavailable")
    if not candidate.state_retained:
        reasons.append("state-retention-failed")
    if not candidate.task_completed:
        reasons.append("candidate-incomplete")
    if candidate.task_outcome != baseline.task_outcome:
        reasons.append("task-outcome-regressed")
    if candidate.tool_loops > baseline.tool_loops:
        reasons.append("tool-loops-increased")
    return QualificationDecision(not reasons, tuple(reasons), improvement)
