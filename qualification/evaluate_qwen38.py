from __future__ import annotations

import argparse
import json
from pathlib import Path

from codex_local_harness.roles import (
    AdvisoryRole,
    FailureAnalysisResult,
    MapperResult,
    ScoutResult,
    normalize_single_json_fence,
    parse_result,
)


def evaluate(directory: Path) -> dict[str, dict[str, object]]:
    results: dict[str, dict[str, object]] = {}
    for role in AdvisoryRole:
        path = directory / f"{role.value.replace('-', '_')}.out"
        raw = path.read_text(encoding="utf-8").strip() if path.exists() else ""
        raw_schema_valid = True
        normalized_schema_valid = False
        try:
            parsed = parse_result(role, raw)
            normalized_schema_valid = True
        except ValueError:
            raw_schema_valid = False
            try:
                parsed = parse_result(role, normalize_single_json_fence(raw))
                normalized_schema_valid = True
            except ValueError as exc:
                results[role.value] = {
                    "raw_schema_valid": False,
                    "normalized_schema_valid": False,
                    "expected_finding_recalled": False,
                    "error": str(exc),
                }
                continue
        try:
            recalled = _recall(role, parsed)
            results[role.value] = {
                "raw_schema_valid": raw_schema_valid,
                "normalized_schema_valid": normalized_schema_valid,
                "expected_finding_recalled": recalled,
                "error": None,
            }
        except (OSError, ValueError) as exc:
            results[role.value] = {
                "raw_schema_valid": raw_schema_valid,
                "normalized_schema_valid": normalized_schema_valid,
                "expected_finding_recalled": False,
                "error": str(exc),
            }
    return results


def _recall(role: AdvisoryRole, result: object) -> bool:
    if role is AdvisoryRole.MAPPER and isinstance(result, MapperResult):
        files = set(result.relevant_files) | set(result.existing_tests)
        return {
            "src/codex_local_harness/health.py",
            "src/codex_local_harness/merge_gate.py",
            "src/codex_local_harness/merge_publish.py",
        }.issubset(files)
    if role is AdvisoryRole.TEST_DESIGNER:
        tests = getattr(result, "tests", ())
        return any(
            " is " in f" {test.assertion.lower()} "
            or "identity" in (test.name + test.criterion + test.rationale).lower()
            for test in tests
        )
    if role is AdvisoryRole.FAILURE_ANALYST and isinstance(result, FailureAnalysisResult):
        text = " ".join(
            hypothesis.claim + " " + hypothesis.evidence + " " + hypothesis.next_check
            for hypothesis in result.hypotheses
        ).lower()
        return "lock" in text and any(word in text for word in ("remove", "cleanup", "remain", "artifact", "unlink"))
    if role is AdvisoryRole.SCOUT and isinstance(result, ScoutResult):
        return any(
            finding.severity in {"high", "critical"}
            and finding.criterion == "AC-1"
            and any(word in (finding.claim + " " + finding.evidence).lower() for word in ("prefix", "startswith", "seven", "7"))
            for finding in result.findings
        )
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("outputs", type=Path)
    args = parser.parse_args()
    results = evaluate(args.outputs)
    print(json.dumps(results, indent=2, sort_keys=True))
    return 0 if all(item["normalized_schema_valid"] and item["expected_finding_recalled"] for item in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
