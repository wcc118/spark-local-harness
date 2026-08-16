from __future__ import annotations

import argparse
import json
from pathlib import Path

from codex_local_harness.roles import AdvisoryRole, normalize_single_json_fence, parse_result
from evaluate_qwen38 import _recall


ROLE_MAP = {
    "test-designer": AdvisoryRole.TEST_DESIGNER,
    "failure-analyst": AdvisoryRole.FAILURE_ANALYST,
    "scout": AdvisoryRole.SCOUT,
}


def evaluate(outputs: Path) -> dict:
    metrics = json.loads((outputs / "trial-metrics.json").read_text(encoding="utf-8"))
    trials = []
    for metric in metrics["trials"]:
        raw = (outputs / metric["output_file"]).read_text(encoding="utf-8").strip()
        role = ROLE_MAP[metric["role"]]
        raw_valid = normalized_valid = recall = False
        error = None
        try:
            result = parse_result(role, raw)
            raw_valid = normalized_valid = True
        except ValueError:
            try:
                result = parse_result(role, normalize_single_json_fence(raw))
                normalized_valid = True
            except ValueError as exc:
                result = None
                error = str(exc)
        if metric["control"] == "positive" and result is not None:
            recall = _recall(role, result)
        normalized_object = None
        try:
            normalized_object = json.loads(normalize_single_json_fence(raw))
        except (ValueError, json.JSONDecodeError):
            pass
        false_positive = False
        if metric["control"] == "negative" and isinstance(normalized_object, dict):
            if role is AdvisoryRole.SCOUT:
                false_positive = bool(normalized_object.get("findings"))
            elif role is AdvisoryRole.FAILURE_ANALYST:
                false_positive = bool(normalized_object.get("hypotheses"))
            else:
                text = json.dumps(normalized_object).lower()
                false_positive = any(phrase in text for phrase in ("observed defect", "implementation violates", "known failure"))
        trials.append(metric | {
            "raw_schema_valid": raw_valid,
            "normalized_schema_valid": normalized_valid,
            "expected_finding_recalled": recall if metric["control"] == "positive" else None,
            "false_positive": false_positive if metric["control"] == "negative" else None,
            "validation_error": error,
        })
    roles = {}
    for name in ROLE_MAP:
        role_trials = [item for item in trials if item["role"] == name]
        positives = [item for item in role_trials if item["control"] == "positive"]
        negatives = [item for item in role_trials if item["control"] == "negative"]
        roles[name] = {
            "positive_trials": len(positives),
            "raw_schema_rate": sum(item["raw_schema_valid"] for item in role_trials) / len(role_trials),
            "normalized_schema_rate": sum(item["normalized_schema_valid"] for item in role_trials) / len(role_trials),
            "recall_rate": sum(item["expected_finding_recalled"] for item in positives) / len(positives),
            "false_positive_rate": sum(item["false_positive"] for item in negatives) / len(negatives),
            "mean_wall_seconds": sum(item["wall_seconds"] for item in role_trials) / len(role_trials),
            "total_tokens": sum(item["usage"].get("total_tokens", 0) for item in role_trials),
            "reasoning_tokens": sum(item["usage"].get("completion_tokens_details", {}).get("reasoning_tokens", 0) for item in role_trials),
        }
    return {
        "model": metrics["model"], "temperature": metrics["temperature"],
        "max_tokens": metrics["max_tokens"], "warmup_seconds": metrics["warmup_seconds"],
        "roles": roles, "trials": trials,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("outputs", type=Path)
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()
    result = evaluate(args.outputs)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.summary:
        args.summary.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
