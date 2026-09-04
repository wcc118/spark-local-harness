"""CLI for role-suite v1 execution and analysis."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .framework import (
    SCHEMA_VERSION,
    POLICY_VARIANTS,
    SUITE_CATEGORIES,
    ROLE_TYPES,
    SUITE_CATEGORIES as SUITE_NAMES,
    create_reviewer_cases,
    create_investigator_cases,
    create_test_strategy_cases,
    create_non_coding_cases,
    create_end_to_end_cases,
    SuiteCase,
    SuiteAttempt,
    SuiteSummary,
    compute_suite_metrics,
    create_manifest,
    load_manifest,
    save_manifest,
)
from ..health import append_jsonl
from ..benchmark import FIXTURE_CATEGORIES


def main(argv: list[str] | None = None) -> int:
    """Main entry point for role-suite CLI."""
    parser = argparse.ArgumentParser(
        prog="codex-role-suite",
        description="Role-suite v1 for worker qualification",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Manifest commands
    manifest_parser = subparsers.add_parser("manifest", help="Create or validate manifest")
    manifest_parser.add_argument("--output", "-o", type=Path, required=True, help="Output manifest path")
    manifest_parser.add_argument("--suites", nargs="+", default=["reviewer", "investigator", "test-strategy", "non-coding", "end-to-end"],
                                  choices=["reviewer", "investigator", "test-strategy", "non-coding", "end-to-end"],
                                  help="Suites to include")
    manifest_parser.add_argument("--validate", action="store_true", help="Validate existing manifest")

    # Run command
    run_parser = subparsers.add_parser("run", help="Run suite cases")
    run_parser.add_argument("--manifest", "-m", type=Path, required=True, help="Manifest path")
    run_parser.add_argument("--suite", required=True, choices=["reviewer", "investigator", "test-strategy", "non-coding", "end-to-end"],
                            help="Suite to run")
    run_parser.add_argument("--case", help="Specific case ID (optional)")
    run_parser.add_argument("--repeat", type=int, default=1, help="Repeat count")
    run_parser.add_argument("--timeout", type=int, help="Timeout override (seconds)")
    run_parser.add_argument("--resume", action="store_true", help="Resume from last state")
    run_parser.add_argument("--policy", default="baseline", choices=list(POLICY_VARIANTS.keys()),
                            help="Policy variant")
    run_parser.add_argument("--model", default=os.environ.get("CODEX_LOCAL_MODEL", "vllm/Qwen/Qwen3-Coder-Next-FP8"),
                            help="Model name")
    run_parser.add_argument("--route", default=os.environ.get("CODEX_LOCAL_ROUTE", "coder/complex"),
                            help="Route name")
    run_parser.add_argument("--opencode", default=os.environ.get("CODEX_LOCAL_OPENCODE", "opencode"),
                            help="OpenCode executable")
    run_parser.add_argument("--output", "-o", type=Path, required=True, help="Output directory")
    run_parser.add_argument("--log-dir", type=Path, help="Log directory")

    # Aggregate command
    agg_parser = subparsers.add_parser("aggregate", help="Aggregate results from attempts")
    agg_parser.add_argument("--attempts", "-a", type=Path, required=True, help="Attempts JSONL file")
    agg_parser.add_argument("--output", "-o", type=Path, required=True, help="Output metrics JSONL")

    # Compare command
    cmp_parser = subparsers.add_parser("compare", help="Compare two manifest runs")
    cmp_parser.add_argument("--left", "-l", type=Path, required=True, help="Left attempts JSONL")
    cmp_parser.add_argument("--right", "-r", type=Path, required=True, help="Right attempts JSONL")
    cmp_parser.add_argument("--output", "-o", type=Path, required=True, help="Output comparison JSONL")

    # Verify command
    verify_parser = subparsers.add_parser("verify", help="Verify suite compliance")
    verify_parser.add_argument("--manifest", "-m", type=Path, required=True, help="Manifest path")
    verify_parser.add_argument("--attempts", "-a", type=Path, required=True, help="Attempts JSONL")

    args = parser.parse_args(argv)

    try:
        if args.command == "manifest":
            return cmd_manifest(args)
        elif args.command == "run":
            return cmd_run(args)
        elif args.command == "aggregate":
            return cmd_aggregate(args)
        elif args.command == "compare":
            return cmd_compare(args)
        elif args.command == "verify":
            return cmd_verify(args)
        else:
            parser.print_help()
            return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def cmd_manifest(args: argparse.Namespace) -> int:
    """Create or validate a manifest."""
    if args.validate:
        try:
            manifest = load_manifest(args.output)
            print(f"Manifest valid: {manifest.get('manifest_id', 'unknown')}")
            print(f"Description: {manifest.get('description', 'N/A')}")
            print(f"Cases count: {manifest.get('cases_count', 0)}")
            return 0
        except json.JSONDecodeError as exc:
            print(f"Invalid JSON: {exc}", file=sys.stderr)
            return 1
        except FileNotFoundError:
            print(f"Manifest not found: {args.output}", file=sys.stderr)
            return 1

    manifest = create_manifest(suites=args.suites)
    save_manifest(manifest, args.output)
    print(f"Created manifest: {args.output}")
    print(f"Description: {manifest['description']}")
    print(f"Suites: {', '.join(manifest['suites'])}")
    print(f"Cases count: {manifest['cases_count']}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Run suite cases."""
    # Load manifest
    manifest = load_manifest(args.manifest)
    cases = [SuiteCase(**c) for c in manifest.get("cases", [])]

    # Filter by suite
    suite_cases = [c for c in cases if c.suite == args.suite]

    # Filter by case if specified
    if args.case:
        suite_cases = [c for c in suite_cases if c.case_id == args.case]

    # Apply repeat override
    if args.repeat:
        for c in suite_cases:
            c = c.__replace__(repeat=args.repeat)  # type: ignore[attr-defined]

    # Apply timeout override
    if args.timeout:
        for c in suite_cases:
            c = c.__replace__(timeout_seconds=args.timeout)  # type: ignore[attr-defined]

    # Generate attempts
    attempts: list[SuiteAttempt] = []
    for case in suite_cases:
        for repeat_idx in range(1, case.repeat + 1):
            # Determine case ID with repeat suffix
            attempt_case_id = f"{case.case_id}-r{repeat_idx}"

            # Create placeholder attempt (actual execution would go here)
            attempt = SuiteAttempt(
                attempt_id=f"{case.suite}-{case.case_id}-{repeat_idx:02d}",
                case_id=case.case_id,
                suite=case.suite,
                category=case.category,
                repeat_index=repeat_idx,
                model=args.model,
                route=args.route,
                policy=args.policy,
                elapsed_seconds=0.0,
                state="pending",
                tokens=None,
                tool_calls=0,
                tool_errors=0,
                corrections=None,
                deterministic_correct=None,
                oracle_match=None,
                run_id="",
                observed_at=datetime.now(timezone.utc).isoformat(),
            )
            attempts.append(attempt)

    # Write attempts
    args.output.mkdir(parents=True, exist_ok=True)
    attempts_file = args.output / "attempts.jsonl"
    with attempts_file.open("w", encoding="utf-8") as f:
        for attempt in attempts:
            f.write(json.dumps(attempt.as_dict(), sort_keys=True) + "\n")

    print(f"Generated {len(attempts)} attempts for suite '{args.suite}'")
    print(f"Attempts written to: {attempts_file}")
    return 0


def cmd_aggregate(args: argparse.Namespace) -> int:
    """Aggregate results from attempts."""
    attempts = load_attempts(args.attempts)
    summaries = compute_suite_metrics(attempts)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        for summary in summaries:
            f.write(json.dumps(summary.as_dict(), sort_keys=True) + "\n")

    print(f"Computed metrics for {len(summaries)} case groups")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Compare two manifest runs."""
    left_attempts = load_attempts(args.left)
    right_attempts = load_attempts(args.right)

    # Group by case identity
    left_by_case: dict[str, list[SuiteAttempt]] = {}
    for a in left_attempts:
        key = f"{a.suite}:{a.category}:{a.case_id}"
        left_by_case.setdefault(key, []).append(a)

    right_by_case: dict[str, list[SuiteAttempt]] = {}
    for a in right_attempts:
        key = f"{a.suite}:{a.category}:{a.case_id}"
        right_by_case.setdefault(key, []).append(a)

    comparisons = []
    for case_key, left_group in left_by_case.items():
        if case_key not in right_by_case:
            continue
        right_group = right_by_case[case_key]

        # Compare first attempt from each group
        left = left_group[0]
        right = right_group[0]

        left_elapsed = left.elapsed_seconds if left.state == "completed" else None
        right_elapsed = right.elapsed_seconds if right.state == "completed" else None
        left_tokens = left.tokens.total if left.tokens else None
        right_tokens = right.tokens.total if right.tokens else None
        left_correct = left.deterministic_correct
        right_correct = right.deterministic_correct

        token_reduction = None
        if left_tokens is not None and right_tokens is not None and left_tokens > 0:
            token_reduction = 1.0 - (right_tokens / left_tokens)

        time_reduction = None
        if left_elapsed is not None and right_elapsed is not None and left_elapsed > 0:
            time_reduction = 1.0 - (right_elapsed / left_elapsed)

        comparisons.append({
            "case_key": case_key,
            "left_model": left.model,
            "right_model": right.model,
            "left_route": left.route,
            "right_route": right.route,
            "left_elapsed": left_elapsed,
            "right_elapsed": right_elapsed,
            "left_tokens": left_tokens,
            "right_tokens": right_tokens,
            "left_correct": left_correct,
            "right_correct": right_correct,
            "token_reduction": token_reduction,
            "time_reduction": time_reduction,
        })

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        for comp in comparisons:
            f.write(json.dumps(comp, sort_keys=True) + "\n")

    print(f"Created {len(comparisons)} case comparisons")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Verify suite compliance."""
    manifest = load_manifest(args.manifest)
    attempts = load_attempts(args.attempts)

    expected_cases = set()
    for case in manifest.get("cases", []):
        expected_cases.add(f"{case['suite']}:{case['category']}:{case['case_id']}")

    actual_cases = set()
    for attempt in attempts:
        actual_cases.add(f"{attempt.suite}:{attempt.category}:{attempt.case_id}")

    missing = expected_cases - actual_cases
    extra = actual_cases - expected_cases

    issues = []
    if missing:
        issues.append(f"Missing {len(missing)} expected cases")
    if extra:
        issues.append(f"Extra {len(extra)} unexpected cases")

    if issues:
        for issue in issues:
            print(f"Issue: {issue}", file=sys.stderr)
        return 1

    print("Suite verification passed")
    print(f"Cases: {len(expected_cases)}")
    return 0


def load_attempts(path: Path) -> list[SuiteAttempt]:
    """Load attempts from JSONL file."""
    attempts = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            attempts.append(SuiteAttempt(**data))
    return attempts


if __name__ == "__main__":
    sys.exit(main())
