from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from typing import Any


DEFAULT_STAGE_BUDGETS = {
    "investigation": {"tool_calls": 12, "tokens": 180_000, "seconds": 300},
    "implementation": {"tool_calls": 20, "tokens": 300_000, "seconds": 900},
    "testing": {"tool_calls": 8, "tokens": 120_000, "seconds": 300},
    "review": {"tool_calls": 0, "tokens": 60_000, "seconds": 120},
}
_TEST = re.compile(r"(^|\s|/)(pytest|tox|nox|cargo test|go test|npm test|pnpm test|yarn test)(\s|$)")


def load_stage_budgets(raw: str | None) -> dict[str, dict[str, int]]:
    if not raw:
        return {stage: values.copy() for stage, values in DEFAULT_STAGE_BUDGETS.items()}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("stage budgets must be a JSON object")
    result = {stage: values.copy() for stage, values in DEFAULT_STAGE_BUDGETS.items()}
    for stage, limits in value.items():
        if stage not in result or not isinstance(limits, dict):
            raise ValueError(f"invalid stage budget: {stage}")
        for metric, limit in limits.items():
            if metric not in result[stage] or not isinstance(limit, int) or limit < 0:
                raise ValueError(f"invalid {stage} budget metric: {metric}")
            result[stage][metric] = limit
    return result


class ExecutionDiagnostics:
    """Derive observable stage, repetition, and budget events from tool telemetry."""

    def __init__(self, role: str, budgets: dict[str, dict[str, int]], *, clock=time.monotonic):
        self.role = role
        self.stage = "review" if role == "reviewer" else "investigation"
        self.budgets = budgets
        self.clock = clock
        self.stage_started = clock()
        self.calls: dict[str, str] = {}
        self.fingerprints: Counter[str] = Counter()
        self.stage_calls: Counter[str] = Counter()
        self.stage_tokens: Counter[str] = Counter()
        self.emitted: set[tuple[str, str, str]] = set()

    @staticmethod
    def _fingerprint(part: dict[str, Any]) -> str:
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        inputs = state.get("input") if isinstance(state.get("input"), dict) else {}
        canonical = json.dumps({"tool": part.get("tool"), "input": inputs}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]

    def _event(self, kind: str, severity: str, reason: str, **values: Any) -> dict[str, Any]:
        return {"type": "diagnostic", "timestamp": int(time.time() * 1000), "part": {
            "kind": kind, "severity": severity, "stage": self.stage, "reason": reason, **values,
        }}

    def _transition(self, part: dict[str, Any]) -> dict[str, Any] | None:
        if self.stage == "review":
            return None
        tool = str(part.get("tool") or "")
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        inputs = state.get("input") if isinstance(state.get("input"), dict) else {}
        command = str(inputs.get("command") or "")
        target = "testing" if tool == "bash" and _TEST.search(command) else (
            "implementation" if tool in {"edit", "write", "patch", "apply_patch"} else self.stage
        )
        if target == self.stage:
            return None
        prior = self.stage
        self.stage = target
        self.stage_started = self.clock()
        return self._event("stage-transition", "info", f"observable activity moved from {prior} to {target}", prior_stage=prior)

    def observe(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        output: list[dict[str, Any]] = []
        if event.get("type") == "tool_use":
            transition = self._transition(part)
            if transition:
                output.append(transition)
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            call_id = str(part.get("callID") or part.get("id") or "")
            status = str(state.get("status") or "")
            if call_id and status in {"completed", "error", "failed"} and call_id not in self.calls:
                fingerprint = self._fingerprint(part)
                self.calls[call_id] = fingerprint
                self.fingerprints[fingerprint] += 1
                self.stage_calls[self.stage] += 1
                count = self.fingerprints[fingerprint]
                # For reviewer roles, enforce exploration-loop threshold of 20;
                # for other roles, use threshold of 3. Empty input alone never aborts.
                reviewer_roles = {"reviewer", "convergence-reviewer"}
                threshold = 20 if self.role in reviewer_roles else 3
                if count >= threshold:
                    key = ("repeated-command", fingerprint, str(count))
                    if key not in self.emitted:
                        self.emitted.add(key)
                        output.append(self._event(
                            "exploration-loop", "critical",
                            f"the same observable tool request completed {count} times",
                            fingerprint=fingerprint, count=count,
                        ))
        elif event.get("type") == "step_finish":
            tokens = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
            total = tokens.get("total")
            if isinstance(total, int) and total >= 0:
                self.stage_tokens[self.stage] += total

        limits = self.budgets[self.stage]
        observed = {
            "tool_calls": self.stage_calls[self.stage],
            "tokens": self.stage_tokens[self.stage],
            "seconds": int(self.clock() - self.stage_started),
        }
        for metric, value in observed.items():
            limit = limits[metric]
            if limit <= 0 and value <= 0:
                continue
            severity = "critical" if value > limit else "warning" if limit and value >= limit * 0.8 else None
            if severity:
                key = (f"stage-budget:{self.stage}", metric, severity)
                if key not in self.emitted:
                    self.emitted.add(key)
                    output.append(self._event(
                        "stage-budget", severity,
                        f"{self.stage} {metric} observed at {value} of {limit}",
                        metric=metric, observed=value, limit=limit,
                    ))
        return output
