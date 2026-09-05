#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(item.read_text()) for item in path.glob("attempts/*/*/attempt.json")]


def stats(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "attempts": len(items),
        "artifact_correct": sum(bool(item.get("artifact_correct", item.get("deterministic_correct"))) for item in items),
        "completed_and_correct": sum(
            item.get("state") == "completed"
            and bool(item.get("artifact_correct", item.get("deterministic_correct")))
            for item in items
        ),
        "timeouts": sum(item.get("state") == "timed-out" for item in items),
        "elapsed_seconds": round(sum(float(item.get("elapsed_seconds") or 0) for item in items), 3),
        "tool_calls": sum(int(item.get("tool_calls") or 0) for item in items),
        "tool_errors": sum(int(item.get("tool_errors") or 0) for item in items),
        "tokens_total": sum(int(item.get("tokens_total") or 0) for item in items),
    }


def build(root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "coding-confirmation": plan["coding"]["cases"] * plan["coding"]["confirmation_repeats"],
        "specialist-confirmation": plan["specialist"]["confirmation_cases"] * plan["specialist"]["confirmation_repeats"],
        "lifecycle": plan["lifecycle"]["cases"] * plan["lifecycle"]["repeats"],
    }
    models: dict[str, Any] = {}
    complete = True
    assumptions = plan["codex_displacement_assumptions"]
    for key, model in plan["models"].items():
        tracks: dict[str, Any] = {}
        for track in expected:
            track_rows = rows(root / track / key)
            value = stats(track_rows)
            value["expected_attempts"] = expected[track]
            value["cohort_complete"] = len(track_rows) == expected[track] and all(item.get("state") == "completed" for item in track_rows)
            complete = complete and value["cohort_complete"]
            tracks[track] = value
        rates = {
            track: tracks[track]["completed_and_correct"] / expected[track]
            for track in expected
        }
        holistic = 100 * (
            rates["coding-confirmation"] * plan["holistic_weights"]["coding"]
            + rates["specialist-confirmation"] * plan["holistic_weights"]["specialist"]
            + rates["lifecycle"] * plan["holistic_weights"]["lifecycle"]
        ) if all(tracks[track]["cohort_complete"] for track in tracks) else 0.0
        displacement = (
            tracks["coding-confirmation"]["completed_and_correct"] * assumptions["coding_completed_correct_tokens"]
            + tracks["specialist-confirmation"]["completed_and_correct"] * assumptions["specialist_completed_correct_tokens"]
            + tracks["lifecycle"]["completed_and_correct"] * assumptions["lifecycle_completed_correct_tokens"]
        )
        models[key] = {"model": model, "tracks": tracks, "rates": rates,
                       "holistic_score": round(holistic, 2),
                       "estimated_codex_tokens_displaced": displacement}
    return {"schema_version": 1, "benchmark_id": plan["benchmark_id"],
            "evidence_complete": complete, "models": models,
            "weights": plan["holistic_weights"],
            "displacement_assumptions": assumptions}


def markdown(card: dict[str, Any]) -> str:
    lines = ["# Structured-issue holistic benchmark", "", f"Evidence complete: **{card['evidence_complete']}**", "",
             "| Model | Coding | Specialist | Lifecycle | Holistic | Estimated Codex tokens displaced |",
             "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for key, value in card["models"].items():
        tracks = value["tracks"]
        cells = [f"{tracks[name]['completed_and_correct']}/{tracks[name]['expected_attempts']}"
                 for name in ("coding-confirmation", "specialist-confirmation", "lifecycle")]
        lines.append(f"| {key} | {cells[0]} | {cells[1]} | {cells[2]} | {value['holistic_score']:.2f} | {value['estimated_codex_tokens_displaced']:,} |")
    lines.extend(["", "Holistic score weights correctness only: coding 50%, specialist 35%, lifecycle 15%. Incomplete cohorts score zero.",
                  "Codex-token displacement is an explicit planning estimate, not observed billing.", ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the frozen holistic benchmark scorecard")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=Path(__file__).with_name("plan.json"))
    args = parser.parse_args()
    card = build(args.root, json.loads(args.plan.read_text()))
    args.root.mkdir(parents=True, exist_ok=True)
    (args.root / "scorecard.json").write_text(json.dumps(card, indent=2, sort_keys=True) + "\n")
    (args.root / "scorecard.md").write_text(markdown(card))
    print(json.dumps({"evidence_complete": card["evidence_complete"], "scorecard": str(args.root / "scorecard.json")}))
    return 0 if card["evidence_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
