"""Role-suite v1 runner for executing cases against OpenCode workers."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .framework import (
    SuiteCase,
    SuiteAttempt,
    POLICY_VARIANTS,
)
from ..health import append_jsonl


@dataclass(frozen=True)
class RunResult:
    """Result of a single worker run."""
    run_id: str
    case_id: str
    suite: str
    category: str
    repeat_index: int
    model: str
    route: str
    policy: str
    elapsed_seconds: float
    state: str  # completed, failed, interrupted, cancelled, timed-out
    tool_calls: int
    tool_errors: int
    corrections: int
    events_file: str
    run_log: str


class SuiteRunner:
    """Runner for role-suite v1 cases."""

    def __init__(
        self,
        opencode: str,
        model: str,
        route: str,
        output_dir: Path,
        log_dir: Path | None = None,
        policy: str = "baseline",
        global_timeout: int | None = None,
    ):
        self.opencode = opencode
        self.model = model
        self.route = route
        self.output_dir = output_dir
        self.log_dir = log_dir or output_dir / "logs"
        self.policy = policy
        self.global_timeout = global_timeout

        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def run_case(self, case: SuiteCase, repeat_index: int = 1) -> SuiteAttempt:
        """Run a single case."""
        case_id_with_repeat = f"{case.case_id}-r{repeat_index}"
        run_id = self._generate_run_id(case_id_with_repeat)

        # Determine effective timeout (case-specific or global)
        timeout = case.timeout_seconds or self.global_timeout or 300

        # Prepare task content
        task_content = self._build_task_content(case)

        # Build command
        cmd = [
            self.opencode,
            "run",
            "--format", "json",
            "--model", self.model,
            "--route", self.route,
        ]

        # Add policy-specific flags
        if self.policy == "explicit-stop":
            cmd.extend(["--explicit-stop", "true"])
        elif self.policy == "structured/phased":
            cmd.extend(["--phased", "true"])

        # Write task to temp file and pass to worker
        task_file = self.output_dir / f"{run_id}.task.md"
        task_file.write_text(task_content, encoding="utf-8")
        cmd.extend(["--task-file", str(task_file)])

        # Run the worker
        started = time.monotonic()
        log_file = self.log_dir / f"{run_id}.jsonl"
        events: list[dict[str, Any]] = []

        try:
            process = subprocess.run(
                cmd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )

            # Capture output and parse events
            if process.stdout:
                for line in process.stdout.splitlines():
                    try:
                        event = json.loads(line)
                        if isinstance(event, dict):
                            events.append(event)
                    except json.JSONDecodeError:
                        pass

            exit_code = process.returncode
            elapsed = time.monotonic() - started

            # Determine state
            if exit_code == 0:
                state = "completed"
            elif exit_code == 124:  # timeout
                state = "timed-out"
            elif exit_code == 130:  # keyboard interrupt
                state = "interrupted"
            else:
                state = "failed"

        except subprocess.TimeoutExpired:
            elapsed = timeout
            state = "timed-out"
            exit_code = 124

        # Count tool calls and errors
        tool_events = [e for e in events if e.get("type") == "tool_use"]
        tool_calls = len(tool_events)
        tool_errors = sum(1 for e in tool_events
                         if (e.get("part", {}).get("state", {}).get("status") == "error"))

        # Count corrections (from event metadata if available)
        corrections = 0
        for e in events:
            if e.get("type") == "correction":
                corrections += 1

        # Write events to log
        with log_file.open("w", encoding="utf-8") as f:
            for event in events:
                f.write(json.dumps(event) + "\n")

        # Build attempt record
        attempt = SuiteAttempt(
            attempt_id=run_id,
            case_id=case.case_id,
            suite=case.suite,
            category=case.category,
            repeat_index=repeat_index,
            model=self.model,
            route=self.route,
            policy=self.policy,
            elapsed_seconds=round(elapsed, 3),
            state=state,
            tokens=None,  # Would require token tracking from OpenCode
            tool_calls=tool_calls,
            tool_errors=tool_errors,
            corrections=corrections,
            deterministic_correct=state == "completed",
            oracle_match=None,  # Would require oracle evaluation
            run_id=run_id,
            observed_at=datetime.now(timezone.utc).isoformat(),
        )

        return attempt

    def _generate_run_id(self, case_id_with_repeat: str) -> str:
        """Generate a deterministic run ID."""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        return f"{timestamp}-{case_id_with_repeat}"

    def _build_task_content(self, case: SuiteCase) -> str:
        """Build the task prompt content."""
        parts = [
            f"# {case.suite.upper()} CASE: {case.case_id}",
            f"",
            f"Category: {case.category}",
            f"",
            f"## Context",
            case.context,
            f"",
            f"## Task",
            case.prompt,
            f"",
            f"## Oracle Check",
            case.oracle_check or "N/A",
        ]
        return "\n".join(parts)


def run_suite(
    cases: list[SuiteCase],
    opencode: str,
    model: str,
    route: str,
    output_dir: Path,
    log_dir: Path | None = None,
    policy: str = "baseline",
    global_timeout: int | None = None,
    resume: bool = False,
) -> list[SuiteAttempt]:
    """Run all cases in a suite."""
    runner = SuiteRunner(
        opencode=opencode,
        model=model,
        route=route,
        output_dir=output_dir,
        log_dir=log_dir,
        policy=policy,
        global_timeout=global_timeout,
    )

    attempts_file = output_dir / "attempts.jsonl"
    existing_attempts: list[SuiteAttempt] = []

    # Load existing attempts for resume
    if resume and attempts_file.exists():
        with attempts_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    data = json.loads(line)
                    existing_attempts.append(SuiteAttempt(**data))

    # Track completed case IDs
    completed = {(a.suite, a.category, a.case_id) for a in existing_attempts}

    new_attempts: list[SuiteAttempt] = []

    for case in cases:
        for repeat_idx in range(1, case.repeat + 1):
            # Skip if already completed (resume mode)
            if resume:
                case_key = (case.suite, case.category, case.case_id)
                if case_key in completed:
                    continue

            attempt = runner.run_case(case, repeat_index=repeat_idx)
            new_attempts.append(attempt)

            # Write new attempt incrementally
            with attempts_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(attempt.as_dict(), sort_keys=True) + "\n")

    return new_attempts + existing_attempts


if __name__ == "__main__":
    # CLI entry point
    import argparse

    parser = argparse.ArgumentParser(description="Run role-suite v1 cases")
    parser.add_argument("--manifest", "-m", type=Path, required=True, help="Manifest path")
    parser.add_argument("--suite", required=True, help="Suite to run")
    parser.add_argument("--opencode", default=os.environ.get("CODEX_LOCAL_OPENCODE", "opencode"))
    parser.add_argument("--model", default=os.environ.get("CODEX_LOCAL_MODEL", "vllm/Qwen/Qwen3-Coder-Next-FP8"))
    parser.add_argument("--route", default=os.environ.get("CODEX_LOCAL_ROUTE", "coder/complex"))
    parser.add_argument("--output", "-o", type=Path, required=True, help="Output directory")
    parser.add_argument("--log-dir", type=Path)
    parser.add_argument("--policy", default="baseline", choices=list(POLICY_VARIANTS.keys()))
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--case", help="Specific case to run")

    args = parser.parse_args()

    # Load manifest
    with args.manifest.open("r", encoding="utf-8") as f:
        manifest = json.load(f)

    # Get cases for suite
    cases = []
    for c in manifest.get("cases", []):
        if c.get("suite") == args.suite:
            cases.append(SuiteCase(**c))

    # Filter by case if specified
    if args.case:
        cases = [c for c in cases if c.case_id == args.case]

    print(f"Running {len(cases)} cases for suite '{args.suite}'")

    attempts = run_suite(
        cases=cases,
        opencode=args.opencode,
        model=args.model,
        route=args.route,
        output_dir=args.output,
        log_dir=args.log_dir,
        policy=args.policy,
        global_timeout=args.timeout,
        resume=args.resume,
    )

    print(f"Completed {len(attempts)} attempts")
