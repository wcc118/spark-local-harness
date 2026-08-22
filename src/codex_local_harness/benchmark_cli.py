#!/usr/bin/env python3
"""Benchmark CLI for paired-model comparison."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .benchmark import (
    BenchmarkAttempt,
    BenchmarkFixture,
    BenchmarkManifest,
    PairedSummary,
    BenchmarkMetrics,
    FIXTURE_CATEGORIES,
    SAMPLE_PROMPTS,
)


def main() -> int:
    """Main entry point for benchmark CLI."""
    parser = argparse.ArgumentParser(
        prog="codex-benchmark",
        description="Paired-model benchmark and dashboard comparison",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Create manifest
    manifest_parser = subparsers.add_parser("manifest", help="Create benchmark manifest")
    manifest_parser.add_argument("--output", "-o", type=Path, required=True, help="Output manifest path")
    manifest_parser.add_argument("--fixtures-dir", type=Path, required=True, help="Directory for fixture files")
    manifest_parser.add_argument("--description", default="Paired-model benchmark manifest", help="Manifest description")

    # Validate manifest
    validate_parser = subparsers.add_parser("validate", help="Validate benchmark manifest")
    validate_parser.add_argument("--manifest", "-m", type=Path, required=True, help="Manifest path")

    # Create fixture
    fixture_parser = subparsers.add_parser("fixture", help="Create benchmark fixture")
    fixture_parser.add_argument("--output", "-o", type=Path, required=True, help="Output fixture path")
    fixture_parser.add_argument("--id", "-i", required=True, help="Fixture ID")
    fixture_parser.add_argument("--category", required=True, choices=list(FIXTURE_CATEGORIES.keys()), help="Fixture category")
    fixture_parser.add_argument("--prompt", required=True, help="Fixture prompt")
    fixture_parser.add_argument("--context", default="", help="Fixture context")

    # Run benchmark
    run_parser = subparsers.add_parser("run", help="Execute benchmark attempt")
    run_parser.add_argument("--manifest", "-m", type=Path, required=True, help="Manifest path")
    run_parser.add_argument("--model", required=True, help="Model name")
    run_parser.add_argument("--route", required=True, help="Route name")
    run_parser.add_argument("--worker-command", help="Worker command template")
    run_parser.add_argument("--resume", action="store_true", help="Resume only matching completed attempts")

    # Compare
    compare_parser = subparsers.add_parser("compare", help="Create paired summary")
    compare_parser.add_argument("--left", "-l", type=Path, required=True, help="Left attempts JSONL")
    compare_parser.add_argument("--right", "-r", type=Path, required=True, help="Right attempts JSONL")
    compare_parser.add_argument("--output", "-o", type=Path, required=True, help="Output summary JSONL")

    # Metrics
    metrics_parser = subparsers.add_parser("metrics", help="Compute aggregate metrics")
    metrics_parser.add_argument("--attempts", "-a", type=Path, required=True, help="Attempts JSONL")
    metrics_parser.add_argument("--output", "-o", type=Path, required=True, help="Output metrics JSONL")
    metrics_parser.add_argument("--min-sample", type=int, default=2, help="Minimum sample size")

    # Dashboard snapshot
    snapshot_parser = subparsers.add_parser("snapshot", help="Generate dashboard snapshot from benchmark data")
    snapshot_parser.add_argument("--manifest", "-m", type=Path, required=True, help="Manifest path")
    snapshot_parser.add_argument("--attempts", "-a", type=Path, required=True, help="Attempts JSONL")
    snapshot_parser.add_argument("--summaries", "-s", type=Path, required=True, help="Paired summaries JSONL")
    snapshot_parser.add_argument("--output", "-o", type=Path, required=True, help="Output snapshot JSON")

    # Materialize fixtures
    materialize_parser = subparsers.add_parser("materialize", help="Materialize fixtures to files")
    materialize_parser.add_argument("--manifest", "-m", type=Path, required=True, help="Manifest path")
    materialize_parser.add_argument("--output-dir", "-o", type=Path, required=True, help="Output directory")

    args = parser.parse_args()

    try:
        if args.command == "manifest":
            return cmd_manifest(args)
        elif args.command == "validate":
            return cmd_validate(args)
        elif args.command == "fixture":
            return cmd_fixture(args)
        elif args.command == "run":
            return cmd_run(args)
        elif args.command == "compare":
            return cmd_compare(args)
        elif args.command == "metrics":
            return cmd_metrics(args)
        elif args.command == "snapshot":
            return cmd_snapshot(args)
        elif args.command == "materialize":
            return cmd_materialize(args)
        else:
            parser.print_help()
            return 1
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def cmd_manifest(args: argparse.Namespace) -> int:
    """Create a new benchmark manifest."""
    fixtures_dir = args.fixtures_dir
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    manifest = BenchmarkManifest(
        manifest_id="benchmark-manifest-v1",
        description=args.description,
        fixtures_count=0,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(manifest.as_dict(), f, indent=2, sort_keys=True)

    print(f"Created manifest: {args.output}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate a benchmark manifest."""
    try:
        with args.manifest.open("r", encoding="utf-8") as f:
            data = json.load(f)
        manifest = BenchmarkManifest.from_dict(data)
        print(f"Manifest valid: {manifest.manifest_id}")
        print(f"Description: {manifest.description}")
        print(f"Fixtures count: {manifest.fixtures_count}")
        return 0
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Validation error: {exc}", file=sys.stderr)
        return 1


def cmd_fixture(args: argparse.Namespace) -> int:
    """Create a benchmark fixture."""
    fixture = BenchmarkFixture.create(
        fixture_id=args.id,
        category=args.category,
        prompt=args.prompt,
        context=args.context,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(fixture.as_dict(), f, indent=2, sort_keys=True)

    print(f"Created fixture: {args.output}")
    print(f"Identity hash: {fixture.identity_hash}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Execute a benchmark attempt."""
    # This is a placeholder - actual implementation would run worker commands
    print("Run command: worker execution placeholder")
    print(f"Model: {args.model}")
    print(f"Route: {args.route}")
    print(f"Resume: {args.resume}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    """Create paired comparison summary."""
    left_attempts = _load_attempts(args.left)
    right_attempts = _load_attempts(args.right)

    summaries = []
    seen = set()

    for left in left_attempts:
        for right in right_attempts:
            if left.identity_hash != right.identity_hash:
                continue

            key = (left.identity_hash, left.attempt_id, right.attempt_id)
            if key in seen:
                continue
            seen.add(key)

            left_tokens = left.tokens.total if left.tokens else None
            right_tokens = right.tokens.total if right.tokens else None
            left_elapsed = left.elapsed_seconds if left.state == "completed" else None
            right_elapsed = right.elapsed_seconds if right.state == "completed" else None

            left_correct = left.state == "completed"
            right_correct = right.state == "completed"

            # Correctness-first rule: if one succeeded and other failed
            correct_first = left_correct and not right_correct
            tie = left_correct == right_correct and left_correct
            inconclusive = not left_correct and not right_correct

            token_reduction = None
            if left_tokens is not None and right_tokens is not None and left_tokens > 0:
                token_reduction = 1.0 - (right_tokens / left_tokens)

            time_reduction = None
            if left_elapsed is not None and right_elapsed is not None and left_elapsed > 0:
                time_reduction = 1.0 - (right_elapsed / left_elapsed)

            notes = []
            if correct_first:
                notes.append("correctness-first: left succeeded, right failed")
            elif tie:
                notes.append("tie: both succeeded")
            elif inconclusive:
                notes.append("inconclusive: both failed")
            else:
                notes.append("mixed: left succeeded, right failed")

            summary = PairedSummary(
                summary_id=f"pair-{left.attempt_id[:8]}-{right.attempt_id[:8]}",
                fixture_id=left.fixture_id,
                category=left.category,
                left_attempt_id=left.attempt_id,
                right_attempt_id=right.attempt_id,
                left_model=left.model,
                right_model=right.model,
                left_route=left.route,
                right_route=right.route,
                left_tokens=left_tokens,
                right_tokens=right_tokens,
                left_elapsed=left_elapsed,
                right_elapsed=right_elapsed,
                left_correct=left_correct,
                right_correct=right_correct,
                correct_first=correct_first,
                tie=tie,
                inconclusive=inconclusive,
                token_reduction=token_reduction,
                time_reduction=time_reduction,
                notes="; ".join(notes),
            )
            summaries.append(summary)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        for summary in summaries:
            f.write(json.dumps(summary.as_dict(), sort_keys=True) + "\n")

    print(f"Created {len(summaries)} paired summaries")
    return 0


def cmd_metrics(args: argparse.Namespace) -> int:
    """Compute aggregate metrics."""
    attempts = _load_attempts(args.attempts)

    # Group by model/route/category
    groups: dict[tuple[str, str, str], list[BenchmarkAttempt]] = {}
    for attempt in attempts:
        key = (attempt.model, attempt.route, attempt.category)
        groups.setdefault(key, []).append(attempt)

    metrics = []
    for (model, route, category), group in groups.items():
        runs = len(group)
        success_count = sum(1 for a in group if a.state == "completed")

        elapsed_values = [a.elapsed_seconds for a in group if a.state == "completed"]
        token_values = [a.tokens.total for a in group if a.tokens and a.tokens.total > 0]
        correction_values = [a.corrections for a in group]
        error_values = [a.tool_errors for a in group]

        success_rate = success_count / runs if runs > 0 else None
        mean_elapsed = statistics.mean(elapsed_values) if elapsed_values else None
        mean_tokens = statistics.mean(token_values) if token_values else None
        mean_corrections = statistics.mean(correction_values) if correction_values else None
        mean_tool_errors = statistics.mean(error_values) if error_values else None

        sample_warning = None
        if runs < args.min_sample:
            sample_warning = f"sample size ({runs}) below minimum ({args.min_sample})"

        m = BenchmarkMetrics(
            model=model,
            route=route,
            category=category,
            runs=runs,
            success_rate=success_rate,
            mean_elapsed=mean_elapsed,
            mean_tokens=mean_tokens,
            mean_corrections=mean_corrections,
            mean_tool_errors=mean_tool_errors,
            sample_warning=sample_warning,
        )
        metrics.append(m)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        for m in metrics:
            f.write(json.dumps(m.as_dict(), sort_keys=True) + "\n")

    print(f"Computed metrics for {len(metrics)} groups")
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Generate dashboard snapshot from benchmark data."""
    # Load benchmark data
    with args.manifest.open("r", encoding="utf-8") as f:
        data = json.load(f)
    manifest = BenchmarkManifest.from_dict(data)

    attempts = _load_attempts(args.attempts)
    summaries = _load_summaries(args.summaries)

    # Compute summary stats
    total_runs = len(attempts)
    completed_runs = sum(1 for a in attempts if a.state == "completed")
    success_rate = completed_runs / total_runs if total_runs > 0 else None

    # Group by model
    model_stats: dict[str, dict[str, Any]] = {}
    for attempt in attempts:
        model = attempt.model
        if model not in model_stats:
            model_stats[model] = {"runs": 0, "completed": 0, "tokens": [], "elapsed": []}
        model_stats[model]["runs"] += 1
        if attempt.state == "completed":
            model_stats[model]["completed"] += 1
        if attempt.tokens and attempt.tokens.total > 0:
            model_stats[model]["tokens"].append(attempt.tokens.total)
        if attempt.state == "completed":
            model_stats[model]["elapsed"].append(attempt.elapsed_seconds)

    model_results = []
    for model, stats in model_stats.items():
        tokens_mean = statistics.mean(stats["tokens"]) if stats["tokens"] else None
        elapsed_mean = statistics.mean(stats["elapsed"]) if stats["elapsed"] else None
        model_results.append({
            "model": model,
            "runs": stats["runs"],
            "completed": stats["completed"],
            "tokens_mean": tokens_mean,
            "elapsed_mean": elapsed_mean,
        })

    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "manifest_id": manifest.manifest_id,
        "description": manifest.description,
        "total_runs": total_runs,
        "completed_runs": completed_runs,
        "success_rate": success_rate,
        "model_results": model_results,
        "paired_summaries": [s.as_dict() for s in summaries],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, sort_keys=True)

    print(f"Generated snapshot: {args.output}")
    return 0


def cmd_materialize(args: argparse.Namespace) -> int:
    """Materialize fixtures to files."""
    with args.manifest.open("r", encoding="utf-8") as f:
        data = json.load(f)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    fixture_dir = args.output_dir / "fixtures"
    fixture_dir.mkdir(exist_ok=True)

    print(f"Materialized to: {args.output_dir}")
    return 0


def _load_attempts(path: Path) -> list[BenchmarkAttempt]:
    """Load attempts from JSONL file."""
    attempts = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            attempts.append(BenchmarkAttempt(**data))
    return attempts


def _load_summaries(path: Path) -> list[PairedSummary]:
    """Load paired summaries from JSONL file."""
    summaries = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            summaries.append(PairedSummary(**data))
    return summaries


if __name__ == "__main__":
    sys.exit(main())
