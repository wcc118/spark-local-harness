#!/usr/bin/env python3
"""Execute DeepSeek prompt-ablation experiment through OpenCode.

This runner extends v2 with:
- Challenge subset selection (25-30 cases)
- Four policy variants for DeepSeek
- Collision-safe model/policy/repeat artifact recording
- Identity validation (oracle answers not exposed to model)
- Comprehensive aggregation metrics
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..v2.cases import Case, all_cases as v2_all_cases
from .challenge_set import (
    get_challenge_cases,
    get_challenge_meta,
    get_ambiguous_case_ids,
    DEEPSEEK_MISSES_STRUCTURED_V2,
)
from .policies import POLICIES, build_full_prompt, POLICY_CATEGORIES

# Policy variants for ablation study
POLICY_VARIANTS = tuple(POLICIES.keys())


def digest(value: Any) -> str:
    """Return SHA-256 digest of JSON-serialized value."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def identity_hash(case: Case, policy: str) -> str:
    """Compute identity hash binding public case, private oracle, and policy."""
    public = {
        "case_id": case.case_id,
        "suite": case.suite,
        "category": case.category,
        "evidence": case.evidence,
        "timeout": case.timeout,
    }
    private = {
        "expected": case.expected,
        "forbidden": case.forbidden,
    }
    policy_prompt = POLICIES.get(policy, "")
    return digest({"public": public, "oracle": private, "policy": policy_prompt})


def normalize_events(raw: str) -> tuple[int, int, int]:
    """Extract tool calls, errors, and tokens from raw event stream."""
    calls: set[str] = set()
    errors: set[str] = set()
    tokens = 0
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = event.get("part", {}) if isinstance(event, dict) else {}
        if isinstance(part, dict) and part.get("type") == "tool":
            cid = str(part.get("callID") or part.get("id") or len(calls))
            calls.add(cid)
            if (part.get("state") or {}).get("status") == "error":
                errors.add(cid)
        tok = part.get("tokens") if isinstance(part, dict) else None
        if isinstance(tok, dict):
            tokens += int(tok.get("total") or 0)
    return len(calls), len(errors), tokens


def score(case: Case, workspace: Path, before: dict[str, str]) -> tuple[bool, dict[str, Any]]:
    """Score result against oracle. Returns (correct, oracle_result)."""
    result_path = workspace / "result.json"
    try:
        result = json.loads(result_path.read_text())
        answers = result.get("answers", [])
        valid = isinstance(answers, list) and all(isinstance(x, str) for x in answers)
    except (OSError, ValueError, KeyError, TypeError):
        return False, {"reason": "missing-or-invalid-result"}

    after = {
        p.relative_to(workspace).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in workspace.rglob("*") if p.is_file() and p.name != "result.json"
    }
    mutated = after != before

    got = set(answers)
    correct = valid and not mutated and got == set(case.expected) and not (got & set(case.forbidden))

    return correct, {
        "answers": sorted(got),
        "expected_count": len(case.expected),
        "workspace_mutated": mutated,
    }


def run_case(
    case: Case,
    args: argparse.Namespace,
    repeat: int,
    policy: str,
) -> dict[str, Any]:
    """Execute a single case through OpenCode worker."""
    ident = identity_hash(case, policy)
    slug = hashlib.sha256(
        f"{args.model}:{policy}:{case.case_id}:r{repeat}".encode()
    ).hexdigest()[:12]

    attempt_dir = args.output / "attempts" / slug / case.case_id
    attempt_file = attempt_dir / "attempt.json"

    # Resume support with identity verification
    if args.resume and attempt_file.exists():
        old = json.loads(attempt_file.read_text())
        if old.get("identity_hash") != ident:
            raise ValueError(f"identity mismatch for {case.case_id}")
        return old

    workspace = attempt_dir / "workspace"
    if attempt_dir.exists():
        shutil.rmtree(attempt_dir)
    workspace.mkdir(parents=True)

    # Write evidence (read-only for model)
    evidence_content = json.dumps(case.evidence, indent=2, sort_keys=True) + "\n"
    (workspace / "evidence.json").write_text(evidence_content)

    before = {"evidence.json": hashlib.sha256((workspace / "evidence.json").read_bytes()).hexdigest()}

    timeout = args.timeout or case.timeout

    # Build full prompt with role and policy
    evidence_path = workspace / "evidence.json"
    result_path = workspace / "result.json"
    full_prompt = build_full_prompt(policy, case.category, str(evidence_path), str(result_path))

    cmd = [
        args.opencode,
        "run",
        "--format",
        "json",
        "--model",
        args.model,
        full_prompt,
    ]

    started = time.monotonic()
    raw = ""
    exit_code = None
    state = "failed"

    try:
        proc = subprocess.run(
            cmd,
            cwd=workspace,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
        raw = proc.stdout or ""
        exit_code = proc.returncode
        state = "completed" if exit_code == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        raw_value = exc.stdout or ""
        raw = raw_value.decode(errors="replace") if isinstance(raw_value, bytes) else raw_value
        state = "timed-out"

    elapsed = time.monotonic() - started
    correct, oracle = score(case, workspace, before)

    # Collision-safe naming: use case_id in filename, slug in directory
    case_slug = hashlib.sha256(case.case_id.encode()).hexdigest()[:8]
    events_file = attempt_dir / f"events_{case_slug}.jsonl"

    calls, errors, tokens = normalize_events(raw)

    (attempt_dir / "events.jsonl").write_text(raw)

    # Build comprehensive record with all ablation metadata
    challenge_meta = get_challenge_meta().get(case.case_id)
    policy_cat = POLICY_CATEGORIES.get(policy, "unknown")

    record = {
        "schema_version": 3,
        "case_id": case.case_id,
        "suite": case.suite,
        "category": case.category,
        "model": args.model,
        "route": args.route,
        "policy": policy,
        "policy_category": policy_cat,
        "repeat": repeat,
        "identity_hash": ident,
        "state": state,
        "terminal_completed": state == "completed",
        "artifact_correct": correct,
        "completed_and_correct": state == "completed" and correct,
        "elapsed_seconds": round(elapsed, 3),
        "tool_calls": calls,
        "tool_errors": errors,
        "tokens_total": tokens,
        "worker_exit_code": exit_code,
        "oracle": oracle,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        # Ablation-specific metadata
        "is_challenge_case": challenge_meta.is_challenge if challenge_meta else False,
        "is_ambiguous_clean_control": challenge_meta.is_ambiguous_clean if challenge_meta else False,
        "is_passing_anchor": challenge_meta.is_passing_anchor if challenge_meta else False,
        "is_deepseek_miss": challenge_meta.is_deepseek_miss if challenge_meta else False,
        "challenge_set_size": len(get_challenge_cases()),
        "policy_variant": policy,
    }

    attempt_file.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")

    # Write separate evidence artifact (without oracle) for audit
    evidence_artifact = {
        "case_id": case.case_id,
        "policy": policy,
        "evidence": case.evidence,
        "observed_at": record["observed_at"],
    }
    evidence_file = attempt_dir / "evidence_artifact.json"
    evidence_file.write_text(json.dumps(evidence_artifact, indent=2, sort_keys=True) + "\n")

    return record


def select_challenge_cases(args: argparse.Namespace) -> list[Case]:
    """Select challenge cases based on filter arguments."""
    if args.challenge_subset == "all":
        cases = get_challenge_cases()
    elif args.challenge_subset == "deepseek_misses":
        v2_cases = {c.case_id: c for c in v2_all_cases()}
        cases = [v2_cases[cid] for cid in sorted(DEEPSEEK_MISSES_STRUCTURED_V2) if cid in v2_cases]
    elif args.challenge_subset == "passing_anchors":
        meta = get_challenge_meta()
        cases = [c.base_case for c in meta.values() if c.is_passing_anchor]
    elif args.challenge_subset == "ambiguous_controls":
        meta = get_challenge_meta()
        cases = [c.base_case for c in meta.values() if c.is_ambiguous_clean]
    else:
        cases = get_challenge_cases()

    # Apply additional filters
    if args.suite:
        cases = [c for c in cases if c.suite == args.suite]

    if args.case:
        case_set = set(args.case)
        cases = [c for c in cases if c.case_id in case_set]

    return cases


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute comprehensive aggregation metrics."""
    # Basic counts
    attempts = len(records)
    artifact_correct = sum(1 for r in records if r.get("artifact_correct", False))
    completed_and_correct = sum(1 for r in records if r.get("completed_and_correct", False))
    timeouts = sum(1 for r in records if r.get("state") == "timed-out")
    failures = sum(1 for r in records if r.get("state") == "failed")

    # Time metrics
    elapsed_seconds = round(sum(r.get("elapsed_seconds", 0) for r in records), 3)

    # Tool metrics
    tool_calls = sum(r.get("tool_calls", 0) for r in records)
    tool_errors = sum(r.get("tool_errors", 0) for r in records)
    tokens_total = sum(r.get("tokens_total", 0) for r in records)

    # Role-based breakdowns
    categories = set(r.get("category", "unknown") for r in records)
    category_stats = {}
    for cat in categories:
        cat_records = [r for r in records if r.get("category") == cat]
        cat_correct = sum(1 for r in cat_records if r.get("artifact_correct", False))
        cat_completed = sum(1 for r in cat_records if r.get("state") == "completed")
        category_stats[cat] = {
            "attempts": len(cat_records),
            "correct": cat_correct,
            "completed": cat_completed,
            "correct_rate": round(cat_correct / len(cat_records), 3) if cat_records else None,
        }

    # Policy variant stats
    policies = set(r.get("policy", "unknown") for r in records)
    policy_stats = {}
    for pol in policies:
        pol_records = [r for r in records if r.get("policy") == pol]
        pol_correct = sum(1 for r in pol_records if r.get("artifact_correct", False))
        policy_stats[pol] = {
            "attempts": len(pol_records),
            "correct": pol_correct,
            "correct_rate": round(pol_correct / len(pol_records), 3) if pol_records else None,
        }

    # Challenge subset stats
    challenge_records = [r for r in records if r.get("is_challenge_case", False)]
    ambiguous_ids = get_ambiguous_case_ids()
    non_ambiguous_challenge = [
        r for r in challenge_records
        if r.get("case_id") not in ambiguous_ids
    ]

    challenge_correct = sum(1 for r in challenge_records if r.get("artifact_correct", False))
    challenge_completed = sum(1 for r in challenge_records if r.get("state") == "completed")

    # Promotion-scoring set (excluding ambiguous controls)
    promo_correct = sum(1 for r in non_ambiguous_challenge if r.get("artifact_correct", False))
    promo_completed = sum(1 for r in non_ambiguous_challenge if r.get("state") == "completed")

    # DeepSeek miss recovery
    deepseek_miss_records = [
        r for r in records
        if r.get("is_deepseek_miss", False) and r.get("artifact_correct", False)
    ]

    # Consistency across repeats (for same case+policy combinations)
    case_policy_pairs: dict[tuple[str, str], list[dict]] = {}
    for r in records:
        key = (r.get("case_id", "unknown"), r.get("policy", "unknown"))
        case_policy_pairs.setdefault(key, []).append(r)

    consistent_count = sum(
        1 for pairs in case_policy_pairs.values()
        if len(pairs) > 1 and len(set(p["artifact_correct"] for p in pairs)) == 1
    )

    return {
        "attempts": attempts,
        "artifact_correct": artifact_correct,
        "completed_and_correct": completed_and_correct,
        "timeouts": timeouts,
        "failures": failures,
        "elapsed_seconds": elapsed_seconds,
        "tool_calls": tool_calls,
        "tool_errors": tool_errors,
        "tokens_total": tokens_total,
        "category_stats": category_stats,
        "policy_stats": policy_stats,
        "challenge": {
            "total_attempts": len(challenge_records),
            "challenge_correct": challenge_correct,
            "challenge_completed": challenge_completed,
            "correct_rate": round(challenge_correct / len(challenge_records), 3) if challenge_records else None,
        },
        "promotion_scored": {
            "attempts": len(non_ambiguous_challenge),
            "promo_correct": promo_correct,
            "promo_completed": promo_completed,
            "correct_rate": round(promo_correct / len(non_ambiguous_challenge), 3) if non_ambiguous_challenge else None,
        },
        "deepseek_miss_recovery": {
            "total_misses": sum(1 for r in records if r.get("is_deepseek_miss", False)),
            "recovered": len(deepseek_miss_records),
            "recovery_rate": round(len(deepseek_miss_records) / sum(1 for r in records if r.get("is_deepseek_miss", False)), 3)
            if any(r.get("is_deepseek_miss", False) for r in records) else None,
        },
        "consistency": {
            "total_case_policy_pairs": len(case_policy_pairs),
            "consistent_pairs": consistent_count,
            "consistency_rate": round(consistent_count / len(case_policy_pairs), 3) if case_policy_pairs else None,
        },
    }


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="DeepSeek prompt-ablation experiment runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Verify command
    verify_parser = subparsers.add_parser("verify", help="Verify frozen corpus shape")

    # Run command
    run_parser = subparsers.add_parser("run", help="Execute ablation experiment")
    run_parser.add_argument(
        "--output", "-o", type=Path, required=True, help="Output directory"
    )
    run_parser.add_argument(
        "--opencode", required=True, help="OpenCode binary path"
    )
    run_parser.add_argument(
        "--model", required=True, help="Model name"
    )
    run_parser.add_argument(
        "--route", required=True, help="Route name"
    )
    run_parser.add_argument(
        "--policy",
        choices=POLICY_VARIANTS,
        action="append",
        help="Policy variant(s) to test (repeat for multiple)",
    )
    run_parser.add_argument(
        "--challenge-subset",
        choices=["all", "deepseek_misses", "passing_anchors", "ambiguous_controls"],
        default="all",
        help="Challenge subset to test",
    )
    run_parser.add_argument(
        "--suite", help="Filter by case suite"
    )
    run_parser.add_argument(
        "--case",
        action="append",
        help="Filter by case ID (repeat for multiple)",
    )
    run_parser.add_argument(
        "--repeat", type=int, default=1, help="Number of repeats per case/policy"
    )
    run_parser.add_argument(
        "--timeout", type=int, help="Timeout override (seconds)"
    )
    run_parser.add_argument(
        "--resume", action="store_true", help="Resume from existing attempts"
    )

    args = parser.parse_args()

    if args.command == "verify":
        cases = get_challenge_cases()
        counts = {
            s: sum(c.suite == s for c in cases) for s in sorted({c.suite for c in cases})
        }
        print(
            json.dumps(
                {
                    "challenge_size": len(cases),
                    "counts": counts,
                    "unique_ids": len({c.case_id for c in cases}) == len(cases),
                    "ambiguous_controls": len(get_ambiguous_case_ids()),
                }
            )
        )
        return 0

    # Validate policies
    policies = args.policy or list(POLICY_VARIANTS)

    # Select cases
    cases = select_challenge_cases(args)

    if not cases:
        print("ERROR: no cases selected", file=__import__("sys").stderr)
        return 1

    # Execute all combinations
    records = []
    for policy in policies:
        for case in cases:
            for repeat in range(1, args.repeat + 1):
                record = run_case(case, args, repeat, policy)
                records.append(record)

    # Output aggregation
    agg = aggregate(records)
    print(json.dumps(agg, sort_keys=True, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
