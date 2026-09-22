from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .diagnostics import Adjudication, LifecycleEvent, read_records
from .health import read_jsonl, summarize
from .read_model import build_run_views, summarize_views


@dataclass(frozen=True)
class HealthPolicy:
    minimum_runs: int = 10
    frontier_share_warning: float = 0.10
    frontier_share_critical: float = 0.20
    autonomy_warning: float = 0.80
    autonomy_critical: float = 0.50
    takeover_rate_warning: float = 0.15
    takeover_rate_critical: float = 0.30
    infrastructure_rate_warning: float = 0.10
    infrastructure_rate_critical: float = 0.20
    corrections_per_run_warning: float = 0.50
    corrections_per_run_critical: float = 1.00

    def __post_init__(self) -> None:
        if self.minimum_runs <= 0:
            raise ValueError("minimum_runs must be positive")
        rates = (
            self.frontier_share_warning,
            self.frontier_share_critical,
            self.autonomy_warning,
            self.autonomy_critical,
            self.takeover_rate_warning,
            self.takeover_rate_critical,
            self.infrastructure_rate_warning,
            self.infrastructure_rate_critical,
        )
        if any(not 0.0 <= value <= 1.0 for value in rates):
            raise ValueError("health policy rates must be between zero and one")
        if self.frontier_share_warning > self.frontier_share_critical:
            raise ValueError("frontier warning must not exceed critical")
        if self.autonomy_warning < self.autonomy_critical:
            raise ValueError("autonomy warning must not be below critical")
        if self.takeover_rate_warning > self.takeover_rate_critical:
            raise ValueError("takeover warning must not exceed critical")
        if self.infrastructure_rate_warning > self.infrastructure_rate_critical:
            raise ValueError("infrastructure warning must not exceed critical")
        if self.corrections_per_run_warning > self.corrections_per_run_critical:
            raise ValueError("correction warning must not exceed critical")


def assess(summary: dict[str, Any], policy: HealthPolicy = HealthPolicy()) -> dict[str, Any]:
    if summary.get("unknown_usage_runs") or summary.get("partial_usage_runs") or summary.get("unknown_frontier_runs"):
        return {"status": "insufficient-data", "reasons": ["incomplete-token-usage"],
                "metrics": {"known_local_tokens": summary.get("known_local_tokens"),
                            "unknown_usage_runs": summary.get("unknown_usage_runs", 0)}, "policy": asdict(policy)}
    runs = int(summary["runs"])
    if runs < policy.minimum_runs:
        return {
            "status": "insufficient-data",
            "reasons": [f"minimum-runs:{runs}/{policy.minimum_runs}"],
            "metrics": _metrics(summary),
            "policy": asdict(policy),
        }

    metrics = _metrics(summary)
    findings: list[tuple[str, str]] = []
    _upper(findings, "frontier-token-share", metrics["frontier_token_share"], policy.frontier_share_warning, policy.frontier_share_critical)
    _lower(findings, "local-autonomy", metrics["local_autonomy_rate"], policy.autonomy_warning, policy.autonomy_critical)
    _upper(findings, "codex-takeover-rate", metrics["codex_takeover_rate"], policy.takeover_rate_warning, policy.takeover_rate_critical)
    _upper(findings, "infrastructure-failure-rate", metrics["infrastructure_failure_rate"], policy.infrastructure_rate_warning, policy.infrastructure_rate_critical)
    _upper(findings, "corrections-per-run", metrics["corrections_per_run"], policy.corrections_per_run_warning, policy.corrections_per_run_critical)
    severity = "red" if any(level == "red" for level, _ in findings) else "yellow" if findings else "green"
    return {
        "status": severity,
        "reasons": [reason for _, reason in findings],
        "metrics": metrics,
        "policy": asdict(policy),
    }


def report(
    path: Path,
    policy: HealthPolicy = HealthPolicy(),
    adjudications_path: Path | None = None,
    lifecycle_path: Path | None = None,
) -> dict[str, Any]:
    summary = summarize(read_jsonl(path))
    result = {"summary": summary, "assessment": assess(summary, policy)}
    if adjudications_path is not None or lifecycle_path is not None:
        views = build_run_views(
            read_jsonl(path),
            read_records(adjudications_path, Adjudication) if adjudications_path else (),
            read_records(lifecycle_path, LifecycleEvent) if lifecycle_path else (),
        )
        diagnostic_summary = summarize_views(views)
        result = {
            "summary": diagnostic_summary,
            "assessment": assess_diagnostics(diagnostic_summary, policy),
            "runs": [view.as_dict() for view in views],
            "worker_claims": result,
        }
    return result


def assess_diagnostics(summary: dict[str, Any], policy: HealthPolicy = HealthPolicy()) -> dict[str, Any]:
    if summary.get("unknown_usage_runs") or summary.get("unknown_frontier_runs"):
        return {"status": "insufficient-data", "reasons": ["unknown-token-usage"],
                "metrics": {"unknown_usage_runs": summary["unknown_usage_runs"]}, "policy": asdict(policy)}
    adjudicated = int(summary["adjudicated_runs"])
    local_tokens = int(summary["local_tokens"])
    frontier_tokens = int(summary["frontier_tokens"])
    total_tokens = local_tokens + frontier_tokens
    metrics = {
        "frontier_token_share": frontier_tokens / total_tokens if total_tokens else 0.0,
        "confirmed_local_autonomy_rate": summary["confirmed_local_autonomy_rate"],
        "corrections_per_adjudicated_run": summary["correction_cycles"] / adjudicated if adjudicated else None,
        "contradiction_rate": summary["contradictions"] / adjudicated if adjudicated else None,
        "unadjudicated_runs": int(summary["unadjudicated_runs"]),
    }
    if adjudicated < policy.minimum_runs:
        return {
            "status": "insufficient-data",
            "reasons": [f"minimum-adjudicated-runs:{adjudicated}/{policy.minimum_runs}"],
            "metrics": metrics,
            "policy": asdict(policy),
        }
    findings: list[tuple[str, str]] = []
    _upper(findings, "frontier-token-share", metrics["frontier_token_share"], policy.frontier_share_warning, policy.frontier_share_critical)
    autonomy = float(metrics["confirmed_local_autonomy_rate"])
    _lower(findings, "confirmed-local-autonomy", autonomy, policy.autonomy_warning, policy.autonomy_critical)
    corrections = float(metrics["corrections_per_adjudicated_run"])
    _upper(findings, "corrections-per-adjudicated-run", corrections, policy.corrections_per_run_warning, policy.corrections_per_run_critical)
    severity = "red" if any(level == "red" for level, _ in findings) else "yellow" if findings else "green"
    return {
        "status": severity,
        "reasons": [reason for _, reason in findings],
        "metrics": metrics,
        "policy": asdict(policy),
    }


def _metrics(summary: dict[str, Any]) -> dict[str, float]:
    runs = int(summary["runs"])
    infrastructure_failures = sum(
        count for name, count in summary["failure_counts"].items() if name.startswith("infrastructure")
    )
    return {
        "frontier_token_share": float(summary["frontier_token_share"]),
        "local_autonomy_rate": float(summary["local_autonomy_rate"]),
        "codex_takeover_rate": summary["codex_takeovers"] / runs if runs else 0.0,
        "infrastructure_failure_rate": infrastructure_failures / runs if runs else 0.0,
        "corrections_per_run": summary["correction_cycles"] / runs if runs else 0.0,
        "median_wall_seconds": float(summary["median_wall_seconds"]),
    }


def _upper(findings: list[tuple[str, str]], name: str, value: float, warning: float, critical: float) -> None:
    if value > critical:
        findings.append(("red", f"{name}:critical:{value:.4f}>{critical:.4f}"))
    elif value > warning:
        findings.append(("yellow", f"{name}:warning:{value:.4f}>{warning:.4f}"))


def _lower(findings: list[tuple[str, str]], name: str, value: float, warning: float, critical: float) -> None:
    if value < critical:
        findings.append(("red", f"{name}:critical:{value:.4f}<{critical:.4f}"))
    elif value < warning:
        findings.append(("yellow", f"{name}:warning:{value:.4f}<{warning:.4f}"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report local harness architecture health")
    parser.add_argument("records", type=Path, help="normalized health JSONL")
    parser.add_argument("--policy", type=Path, help="JSON overrides for HealthPolicy")
    parser.add_argument("--adjudications", type=Path, help="append-only adjudication JSONL")
    parser.add_argument("--lifecycle", type=Path, help="append-only lifecycle JSONL")
    args = parser.parse_args(argv)
    try:
        overrides = json.loads(args.policy.read_text(encoding="utf-8")) if args.policy else {}
        result = report(
            args.records,
            HealthPolicy(**overrides),
            adjudications_path=args.adjudications,
            lifecycle_path=args.lifecycle,
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
