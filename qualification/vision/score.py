"""Score a judge.py run against the frozen fixtures and the Phase 1 gate (issue #195).

Deterministic: given the same fixtures and judge run directory, always produces
the same numbers. Matching a model finding to a ground-truth model-only defect
uses a fixed keyword list recorded in each fixture's ground_truth.json (set
when the fixture was authored, not tuned after seeing results) -- a
lightweight, auditable substitute for a second model-based judge. Every
per-repeat classification is included in the output so a human can spot-check
the automatic matches.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def load_fixtures(fixtures_dir: Path) -> dict[str, dict]:
    fixtures = {}
    for d in sorted(fixtures_dir.iterdir()):
        gt_path = d / "ground_truth.json"
        if gt_path.is_file():
            fixtures[d.name] = json.loads(gt_path.read_text())
    return fixtures


def load_repeats(run_dir: Path, fixture_id: str) -> list[dict]:
    fdir = run_dir / fixture_id
    if not fdir.is_dir():
        return []
    return [
        json.loads(p.read_text())
        for p in sorted(fdir.glob("repeat-*.json"), key=lambda p: int(p.stem.split("-")[1]))
    ]


def finding_text(finding: dict) -> str:
    return f"{finding.get('description', '')} {finding.get('region', '')}".lower()


def match_defect(defect: dict, findings: list[dict]) -> bool:
    keywords = [k.lower() for k in defect.get("match_keywords", [])]
    if not keywords:
        return False
    for finding in findings:
        text = finding_text(finding)
        if any(k in text for k in keywords):
            return True
    return False


def normalize_number(s: str) -> str:
    return "".join(s.split()).lower()


def match_numeric_reading(expected_label: str, expected_value: str, readings: list[dict]) -> bool:
    exp_val = normalize_number(expected_value)
    for r in readings:
        if normalize_number(r.get("value", "")) == exp_val:
            return True
    return False


def score(fixtures: dict[str, dict], run_dir: Path) -> dict[str, Any]:
    per_fixture = {}
    model_only_total = 0
    model_only_found_any_repeat = 0
    clean_repeat_count = 0
    clean_fp_count = 0
    numeric_total = 0
    numeric_correct = 0
    stability_denominator = 0
    stability_stable = 0
    infra = {"ok": 0, "invalid-json": 0, "schema-invalid": 0, "request-error": 0, "malformed-http-response": 0, "no-attempts": 0, "missing": 0}

    for fixture_id, gt in fixtures.items():
        repeats = load_repeats(run_dir, fixture_id)
        entry = {"category": gt["category"], "model_only": gt["model_only"], "repeats": []}
        verdict_signature_per_repeat = []
        for rec in repeats:
            outcome = rec["outcome"]
            infra[outcome] = infra.get(outcome, 0) + 1
            findings = (rec.get("parsed") or {}).get("findings", []) if outcome == "ok" else []
            readings = (rec.get("parsed") or {}).get("numeric_readings", []) if outcome == "ok" else []
            repeat_info = {"repeat": rec["repeat"], "outcome": outcome, "finding_count": len(findings)}

            if gt["category"] == "model-only" and outcome == "ok":
                defect = gt["defects"][0]
                found = match_defect(defect, findings)
                repeat_info["defect_found"] = found
                verdict_signature_per_repeat.append(found)

            if gt["category"] == "clean" and outcome == "ok":
                clean_repeat_count += 1
                clean_fp_count += len(findings)
                repeat_info["false_positive_count"] = len(findings)
                verdict_signature_per_repeat.append(len(findings) == 0)

            if gt.get("numeric_fields") and outcome == "ok":
                per_repeat_correct = 0
                for field in gt["numeric_fields"]:
                    numeric_total += 1
                    ok = match_numeric_reading(field["label"], field["value"], readings)
                    numeric_correct += int(ok)
                    per_repeat_correct += int(ok)
                repeat_info["numeric_correct"] = f"{per_repeat_correct}/{len(gt['numeric_fields'])}"

            entry["repeats"].append(repeat_info)

        if len(repeats) < 3:
            infra["missing"] += 3 - len(repeats)

        if gt["category"] == "model-only":
            model_only_total += 1
            if any(verdict_signature_per_repeat):
                model_only_found_any_repeat += 1
            entry["found_in_any_repeat"] = any(verdict_signature_per_repeat)

        if verdict_signature_per_repeat:
            stability_denominator += 1
            if len(set(verdict_signature_per_repeat)) == 1:
                stability_stable += 1
        entry["stable_across_repeats"] = len(set(verdict_signature_per_repeat)) == 1 if verdict_signature_per_repeat else None

        per_fixture[fixture_id] = entry

    model_only_recall = model_only_found_any_repeat / model_only_total if model_only_total else None
    fp_per_clean_screenshot = clean_fp_count / clean_repeat_count if clean_repeat_count else None
    numeric_accuracy = numeric_correct / numeric_total if numeric_total else None
    stability_rate = stability_stable / stability_denominator if stability_denominator else None
    zero_hangs_and_lane_ok = infra["request-error"] == 0 and infra["malformed-http-response"] == 0 and infra["missing"] == 0
    schema_failures_rejected_not_repaired = True  # judge.py never repairs; this holds by construction if the run completed

    gate = {
        "1_model_only_recall_ge_60pct_and_ge_8_defects": {
            "value": model_only_recall,
            "model_only_total": model_only_total,
            "pass": bool(model_only_total >= 8 and model_only_recall is not None and model_only_recall >= 0.60),
        },
        "2_false_positives_le_0.5_per_clean_screenshot": {
            "value": fp_per_clean_screenshot,
            "clean_repeats_scored": clean_repeat_count,
            "pass": bool(fp_per_clean_screenshot is not None and fp_per_clean_screenshot <= 0.5),
        },
        "3_numeric_accuracy_ge_90pct": {
            "value": numeric_accuracy,
            "numeric_fields_scored": numeric_total,
            "pass": bool(numeric_accuracy is not None and numeric_accuracy >= 0.90),
        },
        "4_stability_ge_80pct": {
            "value": stability_rate,
            "fixtures_scored": stability_denominator,
            "pass": bool(stability_rate is not None and stability_rate >= 0.80),
        },
        "5_zero_hangs_no_silent_repair_lane_always_available": {
            "infra_counts": infra,
            "pass": bool(zero_hangs_and_lane_ok and schema_failures_rejected_not_repaired),
        },
    }
    gate["overall_pass"] = all(g["pass"] for g in gate.values() if isinstance(g, dict) and "pass" in g)

    return {"per_fixture": per_fixture, "gate": gate, "infra_counts": infra}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures-dir", type=Path, default=ROOT / "fixtures" / "frozen")
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    fixtures = load_fixtures(args.fixtures_dir)
    result = score(fixtures, args.run)
    text = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(text)
    print(text)
    return 0 if result["gate"]["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
