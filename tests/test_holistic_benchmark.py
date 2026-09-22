from __future__ import annotations

import importlib.util
import json
from argparse import Namespace
from pathlib import Path

from qualification.holistic.lifecycle import all_cases
from qualification.holistic.run import aggregate, run_case
from qualification.holistic.scorecard import build


ROOT = Path(__file__).parents[1]
PAIRED = ROOT / "qualification" / "paired-model"
SPEC = importlib.util.spec_from_file_location("paired_structured", PAIRED / "run.py")
PAIRED_RUN = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(PAIRED_RUN)


def test_every_coding_case_renders_complete_public_issue_without_oracle_leakage():
    manifest = PAIRED_RUN.load_manifest(PAIRED / "manifest.json")
    assert len(manifest["cases"]) == 10
    for case in manifest["cases"]:
        task = (PAIRED / case["task"]).read_text()
        rendered = PAIRED_RUN.structured_issue(case, task)
        for heading in ("Objective", "Context", "Scope", "Acceptance criteria",
                        "Constraints and exclusions", "Required verification",
                        "Durable deliverable", "Escalation conditions"):
            assert f"## {heading}" in rendered
        assert "oracle.py" not in rendered
        assert "_lock attribute" not in rendered


def test_identity_binds_effective_contract_but_is_model_independent():
    manifest = PAIRED_RUN.load_manifest(PAIRED / "manifest.json")
    case = manifest["cases"][0]
    first = PAIRED_RUN.identity(PAIRED / "manifest.json", manifest, case, "contract one")
    second = PAIRED_RUN.identity(PAIRED / "manifest.json", manifest, case, "contract two")
    assert first["identity_hash"] != second["identity_hash"]
    assert first["prompt_hash"] != second["prompt_hash"]
    assert "model" not in first


def test_lifecycle_worker_executes_and_scores_all_private_outcomes(tmp_path: Path):
    worker = tmp_path / "fake-opencode"
    worker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib\n"
        "e=json.loads(pathlib.Path('evidence.json').read_text())\n"
        "s=e['state']; obj=e['objective']\n"
        "if s.get('review_findings'): a='BLOCK_REVIEW'\n"
        "elif s.get('test_evidence', 'present') is None: a='REQUEST_EVIDENCE'\n"
        "elif 'cannot both hold' in s.get('investigation',''): a='ESCALATE_REQUIREMENTS'\n"
        "elif s.get('investigation') == 'not performed': a='INVESTIGATE_FIRST'\n"
        "else: a='READY_TO_IMPLEMENT'\n"
        "pathlib.Path('result.json').write_text(json.dumps({'answers':[a],'evidence':['bounded']}))\n"
    )
    worker.chmod(0o755)
    args = Namespace(output=tmp_path / "out", opencode=str(worker), model="fake/model",
                     route="test", timeout=10, resume=False)
    results = [run_case(case, args, 1) for case in all_cases()]
    assert aggregate(results)["completed_and_correct"] == 5
    assert all(result["model"] == "fake/model" for result in results)


def test_scorecard_fails_closed_until_both_model_cohorts_exist(tmp_path: Path):
    plan = json.loads((ROOT / "qualification/holistic/plan.json").read_text())
    card = build(tmp_path, plan)
    assert card["evidence_complete"] is False
    assert all(model["holistic_score"] == 0 for model in card["models"].values())


def test_scorecard_counts_terminal_failure_as_zero_not_missing_evidence(tmp_path: Path):
    plan = json.loads((ROOT / "qualification/holistic/plan.json").read_text())
    for model_key in plan["models"]:
        for track, count in (("coding-confirmation", 10),
                             ("specialist-confirmation", 62),
                             ("lifecycle", 5)):
            for index in range(count):
                path = tmp_path / track / model_key / "attempts" / str(index) / "case" / "attempt.json"
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({
                    "state": "timed-out" if index == 0 else "completed",
                    "artifact_correct": False,
                }))
    card = build(tmp_path, plan)
    assert card["evidence_complete"] is True
    assert all(model["holistic_score"] == 0 for model in card["models"].values())
    assert card["models"]["coder-next"]["tracks"]["coding-confirmation"]["timeouts"] == 1
