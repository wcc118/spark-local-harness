from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1] / "qualification" / "paired-model"
SPEC = importlib.util.spec_from_file_location("paired_benchmark", ROOT / "run.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def test_manifest_has_ten_real_cases_and_identity_covers_task_check_policy_source():
    manifest = MODULE.load_manifest(ROOT / "manifest.json")
    assert len(manifest["cases"]) == 10
    identities = [MODULE.identity(ROOT / "manifest.json", manifest, case) for case in manifest["cases"]]
    assert len({item["identity_hash"] for item in identities}) == 10
    assert all(len(item[key]) == 64 for item in identities for key in ("source_hash", "task_hash", "check_hash", "policy_hash"))


def test_manifest_rejects_task_traversal(tmp_path: Path):
    payload = json.loads((ROOT / "manifest.json").read_text())
    payload["cases"][0]["task"] = "../outside.md"
    (tmp_path / "input").mkdir()
    (tmp_path / "manifest.json").write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="invalid task path"):
        MODULE.load_manifest(tmp_path / "manifest.json")


def test_manifest_rejects_case_id_path_traversal(tmp_path: Path):
    payload = json.loads((ROOT / "manifest.json").read_text())
    payload["cases"][0]["id"] = "../../escape"
    (tmp_path / "input").mkdir()
    for case in payload["cases"]:
        task = tmp_path / case["task"]
        task.parent.mkdir(parents=True, exist_ok=True)
        task.write_text("task")
    (tmp_path / "manifest.json").write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="malformed"):
        MODULE.load_manifest(tmp_path / "manifest.json")


def test_event_normalization_deduplicates_tool_lifecycle_and_preserves_unknown_tokens():
    events = [
        {"type":"tool_use","part":{"callID":"one","tool":"bash","state":{"status":"running"}}},
        {"type":"tool_use","part":{"callID":"one","tool":"bash","state":{"status":"completed"}}},
        {"type":"permission.asked","properties":{"id":"p1"}},
        {"type":"permission.asked","properties":{"id":"p1"}},
        {"type":"permission.replied","properties":{"id":"p1"}},
    ]
    result = MODULE.normalize_events(events)
    assert result["tool_calls"] == 1
    assert result["completed_tool_calls"] == 1
    assert result["permissions_requested"] == 1
    assert result["interventions"] == 1
    assert result["tokens_total"] is None


def test_compare_preserves_routes_until_all_ten_identity_matched_pairs(tmp_path: Path, capsys):
    args = type("Args", (), {"manifest":ROOT / "manifest.json", "output":tmp_path, "left":"left", "right":"right"})()
    assert MODULE.command_compare(args) == 2
    comparison = json.loads((tmp_path / "comparison.json").read_text())
    assert comparison["recommendation"]["current_routes_preserved"] is True
    assert comparison["recommendation"]["evidence_complete"] is False


def test_all_baselines_fail_their_hidden_oracle():
    for case in json.loads((ROOT / "manifest.json").read_text())["cases"]:
        result = subprocess.run(
            [sys.executable, str(ROOT / "oracle.py"), case["id"], str(ROOT / "input")],
            text=True, capture_output=True,
        )
        assert result.returncode != 0, case["id"]
