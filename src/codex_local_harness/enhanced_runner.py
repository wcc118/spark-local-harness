"""Enhanced paired-model runner with repetitions, timeout enforcement, and comprehensive metrics.

This module provides:
- Repetition support for deterministic policy evaluation
- Enforced wall-time timeout in subprocess code
- Retained attempt artifacts with identity validation
- Per-policy/category correctness, completion, timeout, tool/error, elapsed, and token metrics
- No deletion of prior benchmark compatibility
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .health import append_jsonl


SCHEMA_VERSION = 1


def digest_file(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest_tree(path: Path) -> str:
    """Compute SHA-256 hash of a directory tree."""
    digest = hashlib.sha256()
    for item in sorted(
        p for p in path.rglob("*") if p.is_file() and ".git" not in p.parts
    ):
        digest.update(item.relative_to(path).as_posix().encode())
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def normalize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Normalize raw events to consistent metrics."""
    calls: dict[str, dict[str, Any]] = {}
    permissions: set[str] = set()
    questions: set[str] = set()
    replies: set[str] = set()
    tokens: int | None = None

    for event in events:
        kind = str(event.get("type") or "")
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        properties = event.get("properties") if isinstance(event.get("properties"), dict) else {}

        if kind in {"tool_use", "tool.part"} or part.get("type") == "tool":
            call = str(part.get("callID") or part.get("call_id") or part.get("id") or "")
            if call:
                calls[call] = part

        request_id = str(properties.get("id") or event.get("id") or "")
        if kind == "permission.asked" and request_id:
            permissions.add(request_id)
        if kind == "question.asked" and request_id:
            questions.add(request_id)
        if kind in {"permission.replied", "question.replied", "question.rejected"} and request_id:
            replies.add(request_id)
        if kind == "step_finish":
            value = part.get("tokens") if isinstance(part.get("tokens"), dict) else {}
            observed = value.get("total")
            if isinstance(observed, int):
                tokens = (tokens or 0) + observed

    failures = 0
    completed = 0
    timeline = []
    for call, part in calls.items():
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        status = str(state.get("status") or "unknown")
        completed += status == "completed"
        failures += status in {"error", "failed"}
        timeline.append({"call_id": call, "tool": part.get("tool"), "status": status})

    return {
        "tool_calls": len(calls),
        "completed_tool_calls": completed,
        "tool_errors": failures,
        "permissions_requested": len(permissions),
        "questions_asked": len(questions),
        "interventions": len(replies),
        "tokens_total": tokens,
        "timeline": timeline[-100:],
    }


def atomic_write(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON atomically using temp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def compute_identity_hash(
    manifest_path: Path,
    manifest: dict[str, Any],
    case: dict[str, Any],
    policy_variant: str = "baseline",
    model: str = "",
) -> str:
    """Compute identity hash binding manifest, case, policy, and model.

    This ensures that changing any field produces a different identity hash,
    enabling detection of identity mismatches during resume.
    """
    source = (manifest_path.parent / manifest["source"]).resolve()
    task = (manifest_path.parent / case["task"]).resolve()

    values = {
        "source_hash": digest_tree(source),
        "task_hash": digest_file(task),
        "check_hash": hashlib.sha256(
            json.dumps(case["check"], separators=(",", ":")).encode()
        ).hexdigest(),
        "policy_hash": hashlib.sha256(
            manifest.get("permission_profile", "").encode()
        ).hexdigest(),
        "policy_variant": policy_variant,
        "model": model,
    }
    return hashlib.sha256(
        json.dumps(values, sort_keys=True).encode()
    ).hexdigest()


class EnhancedRunner:
    """Enhanced paired-model runner with repetitions and comprehensive metrics."""

    def __init__(
        self,
        output_dir: Path,
        opencode_path: str = "opencode",
        agent: str = "coder",
        base_timeout: int = 180,
        enforce_timeout: bool = True,
    ):
        self.output_dir = output_dir
        self.opencode_path = opencode_path
        self.agent = agent
        self.base_timeout = base_timeout
        self.enforce_timeout = enforce_timeout

    def run_case(
        self,
        manifest_path: Path,
        manifest: dict[str, Any],
        case: dict[str, Any],
        policy_variant: str = "baseline",
        instruction_prefix: Path | None = None,
        repetition: int = 1,
        resume: bool = False,
        timeout_override: int | None = None,
    ) -> dict[str, Any]:
        """Run a single case with repetition tracking.

        Args:
            manifest_path: Path to manifest.json
            manifest: Loaded manifest data
            case: Case definition from manifest
            policy_variant: Policy variant name
            instruction_prefix: Optional instruction prefix file
            repetition: Repetition number (1-indexed)
            resume: Whether to resume from existing attempt
            timeout_override: Optional timeout override in seconds

        Returns:
            Attempt result dictionary with all metrics
        """
        case_id = case["id"]
        category = case["category"]
        model = manifest.get("model", "unknown")

        # Compute identity for this specific repetition
        identity_hash = compute_identity_hash(
            manifest_path, manifest, case, policy_variant, model
        )

        # Model+policy+repetition hash for artifact isolation
        slug = hashlib.sha256(
            f"{model}:{policy_variant}:{case_id}:r{repetition}".encode()
        ).hexdigest()[:12]

        attempt_dir = self.output_dir / "attempts" / slug / case_id
        result_path = attempt_dir / "attempt.json"

        # Resume with identity verification
        if resume and result_path.exists():
            old = json.loads(result_path.read_text(encoding="utf-8"))
            if old.get("identity_hash") == identity_hash and old.get("terminal"):
                return old
            raise RuntimeError(f"refusing mismatched resume for {case_id}")

        # Clean existing workspace
        if attempt_dir.exists():
            shutil.rmtree(attempt_dir)
        workspace = attempt_dir / "workspace"
        source_path = (manifest_path.parent / manifest["source"]).resolve()
        shutil.copytree(source_path, workspace)

        # Capture source state before execution
        before = digest_tree(source_path)

        # Load and prep task
        task_path = (manifest_path.parent / case["task"]).resolve()
        task_content = task_path.read_text(encoding="utf-8")

        # Apply instruction prefix
        if instruction_prefix:
            prefix = instruction_prefix.read_text(encoding="utf-8").strip()
            task_content = prefix + "\n\n" + task_content

        # Build run ID
        run_id = f"bench-{case_id}-{slug}-{datetime.now(timezone.utc).isoformat()[:19].replace(':', '')}"

        events_path = attempt_dir / "events.jsonl"

        # Build command
        command = [
            self.opencode_path,
            "run",
            "--format",
            "json",
            "--model",
            model,
            "--agent",
            self.agent,
            "--dir",
            str(workspace),
            task_content,
        ]

        # Execute with timeout
        started = datetime.now(timezone.utc).isoformat()
        began = time.monotonic()
        state = "completed"
        return_code: int | None = None
        stderr = ""

        # Enforce wall-time timeout in subprocess
        timeout = timeout_override or case.get("timeout", self.base_timeout)

        try:
            allowed_environment = {
                key: os.environ[key]
                for key in ("HOME", "PATH", "LANG", "LC_ALL", "TERM", "XDG_CONFIG_HOME",
                           "XDG_DATA_HOME", "XDG_CACHE_HOME")
                if key in os.environ
            }
            allowed_environment["CODEX_BENCHMARK_RUN_ID"] = run_id

            completed = subprocess.run(
                command,
                text=True,
                capture_output=True,
                timeout=timeout,
                env=allowed_environment,
            )
            return_code = completed.returncode
            events_path.write_text(completed.stdout, encoding="utf-8")
            stderr = completed.stderr[-4096:]
            if return_code != 0:
                state = "failed"
        except subprocess.TimeoutExpired as exc:
            state = "timed-out"
            output = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            events_path.write_text(output, encoding="utf-8")
            stderr = "worker timed out"

        elapsed = time.monotonic() - began

        # Run check command
        check_command = [
            str(workspace) if item == "{workspace}" else item
            for item in case["check"]
        ]
        check = subprocess.run(
            check_command,
            cwd=manifest_path.parent,
            text=True,
            capture_output=True,
            timeout=30,
        )
        correct = check.returncode == 0

        # Verify source immutability
        after = digest_tree(source_path)
        if after != before:
            raise RuntimeError("immutable fixture source was modified")

        # Normalize events
        events_list = []
        if events_path.exists():
            for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        events_list.append(value)
                except json.JSONDecodeError:
                    continue
        metrics = normalize_events(events_list)

        # Build result
        result = {
            "schema_version": SCHEMA_VERSION,
            "attempt_id": run_id,
            "run_id": run_id,
            "case_id": case_id,
            "fixture_id": case_id,
            "category": category,
            "model": model,
            "route": manifest.get("route", "default"),
            "policy_variant": policy_variant,
            "repetition": repetition,
            "identity_hash": identity_hash,
            "started_at": started,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(elapsed, 3),
            "state": state,
            "terminal": state in ("completed", "failed", "timed-out"),
            "worker_exit_code": return_code,
            "deterministic_correct": correct,
            "check": {
                "command": check_command,
                "exit_code": check.returncode,
                "stdout": check.stdout[-2048:],
                "stderr": check.stderr[-2048:],
            },
            "stderr": stderr,
            **metrics,
            "workspace": str(workspace),
            "raw_events": str(events_path),
        }

        atomic_write(result_path, result)
        return result

    def run_policy(
        self,
        manifest_path: Path,
        manifest: dict[str, Any],
        policy_variant: str = "baseline",
        instruction_prefix: Path | None = None,
        repetitions: int = 1,
        case_filter: list[str] | None = None,
        resume: bool = False,
        timeout_override: int | None = None,
    ) -> list[dict[str, Any]]:
        """Run all cases under a specific policy.

        Args:
            manifest_path: Path to manifest.json
            manifest: Loaded manifest data
            policy_variant: Policy variant name
            instruction_prefix: Optional instruction prefix file
            repetitions: Number of repetitions per case
            case_filter: Optional list of case IDs to run
            resume: Whether to resume from existing attempts
            timeout_override: Optional timeout override

        Returns:
            List of all attempt results
        """
        selected_cases = manifest["cases"]
        if case_filter:
            selected_cases = [c for c in selected_cases if c["id"] in case_filter]

        results = []
        for _ in range(repetitions):
            for case in selected_cases:
                result = self.run_case(
                    manifest_path=manifest_path,
                    manifest=manifest,
                    case=case,
                    policy_variant=policy_variant,
                    instruction_prefix=instruction_prefix,
                    repetition=_ + 1,
                    resume=resume,
                    timeout_override=timeout_override,
                )
                results.append(result)

        return results

    def aggregate_policy_results(
        self,
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Aggregate results by policy, category, and case.

        Returns metrics including:
        - Correctness per policy/category/case
        - Completion rates
        - Timeout counts
        - Tool error counts
        - Elapsed time sums
        - Token usage sums
        """
        # Group by policy
        by_policy: dict[str, list[dict]] = {}
        for r in results:
            policy = r.get("policy_variant", "unknown")
            by_policy.setdefault(policy, []).append(r)

        policy_stats = {}
        for policy, policy_results in by_policy.items():
            correct = sum(1 for r in policy_results if r.get("deterministic_correct"))
            completed = sum(1 for r in policy_results if r.get("state") == "completed")
            timed_out = sum(1 for r in policy_results if r.get("state") == "timed-out")
            failed = sum(1 for r in policy_results if r.get("state") == "failed")
            elapsed = sum(r.get("elapsed_seconds", 0) for r in policy_results)
            tools = sum(r.get("tool_calls", 0) for r in policy_results)
            tool_errors = sum(r.get("tool_errors", 0) for r in policy_results)
            tokens = sum(r.get("tokens_total") or 0 for r in policy_results)

            policy_stats[policy] = {
                "attempts": len(policy_results),
                "correct": correct,
                "completed": completed,
                "timed_out": timed_out,
                "failed": failed,
                "elapsed_seconds": round(elapsed, 3),
                "tool_calls": tools,
                "tool_errors": tool_errors,
                "tokens_total": tokens,
                "correct_rate": round(correct / len(policy_results), 3) if policy_results else None,
                "completion_rate": round(completed / len(policy_results), 3) if policy_results else None,
            }

        # Group by category
        by_category: dict[str, list[dict]] = {}
        for r in results:
            cat = r.get("category", "unknown")
            by_category.setdefault(cat, []).append(r)

        category_stats = {}
        for cat, cat_results in by_category.items():
            correct = sum(1 for r in cat_results if r.get("deterministic_correct"))
            completed = sum(1 for r in cat_results if r.get("state") == "completed")
            category_stats[cat] = {
                "attempts": len(cat_results),
                "correct": correct,
                "completed": completed,
                "correct_rate": round(correct / len(cat_results), 3) if cat_results else None,
                "completion_rate": round(completed / len(cat_results), 3) if cat_results else None,
            }

        # Per-case stats (for paired comparison)
        by_case: dict[str, list[dict]] = {}
        for r in results:
            cid = r.get("case_id", "unknown")
            by_case.setdefault(cid, []).append(r)

        case_stats = {}
        for cid, case_results in by_case.items():
            correct = sum(1 for r in case_results if r.get("deterministic_correct"))
            completed = sum(1 for r in case_results if r.get("state") == "completed")
            case_stats[cid] = {
                "attempts": len(case_results),
                "correct": correct,
                "completed": completed,
                "correct_rate": round(correct / len(case_results), 3) if case_results else None,
            }

        # Overall stats
        total = len(results)
        total_correct = sum(1 for r in results if r.get("deterministic_correct"))
        total_completed = sum(1 for r in results if r.get("state") == "completed")
        total_timed_out = sum(1 for r in results if r.get("state") == "timed-out")

        return {
            "schema_version": SCHEMA_VERSION,
            "total_attempts": total,
            "total_correct": total_correct,
            "total_completed": total_completed,
            "total_timed_out": total_timed_out,
            "overall_correct_rate": round(total_correct / total, 3) if total else None,
            "overall_completion_rate": round(total_completed / total, 3) if total else None,
            "policy_stats": policy_stats,
            "category_stats": category_stats,
            "case_stats": case_stats,
        }


def run_benchmark(
    manifest_path: Path,
    output_dir: Path,
    model: str,
    route: str,
    policy_variants: list[str] | None = None,
    instruction_prefixes: dict[str, Path] | None = None,
    repetitions: int = 1,
    case_filter: list[str] | None = None,
    resume: bool = False,
    timeout_override: int | None = None,
    instruction_prefix: Path | None = None,
) -> dict[str, Any]:
    """Run a complete benchmark with multiple policies and repetitions.

    This is the main entry point for benchmark execution.

    Args:
        manifest_path: Path to manifest.json
        output_dir: Output directory for artifacts
        model: Model name
        route: Route name
        policy_variants: List of policy variants (defaults to ["baseline"])
        instruction_prefixes: Optional dict mapping policy -> instruction file
        repetitions: Number of repetitions per case/policy
        case_filter: Optional list of case IDs to run
        resume: Whether to resume from existing attempts
        timeout_override: Optional timeout override
        instruction_prefix: Backward-compatible instruction prefix (single policy)

    Returns:
        Aggregated results dictionary
    """
    import json

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # Backward compatibility: single instruction_prefix overrides policy_variants
    if instruction_prefix and not policy_variants:
        policy_variants = ["baseline"]

    if not policy_variants:
        policy_variants = ["baseline"]

    # Build instruction prefixes map
    prefixes = instruction_prefixes or {}
    if instruction_prefix and "baseline" not in prefixes:
        prefixes["baseline"] = instruction_prefix

    runner = EnhancedRunner(
        output_dir=output_dir,
        opencode_path="opencode",
        agent="coder",
        base_timeout=180,
        enforce_timeout=True,
    )

    # Run each policy
    all_results = []
    for policy in policy_variants:
        instruction = prefixes.get(policy)
        results = runner.run_policy(
            manifest_path=manifest_path,
            manifest=manifest,
            policy_variant=policy,
            instruction_prefix=instruction,
            repetitions=repetitions,
            case_filter=case_filter,
            resume=resume,
            timeout_override=timeout_override,
        )
        all_results.extend(results)

    # Aggregate
    aggregated = runner.aggregate_policy_results(all_results)

    # Write aggregated results
    atomic_write(output_dir / "benchmark-results.json", aggregated)

    return aggregated
