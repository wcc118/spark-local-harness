import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    content = subprocess.run(["git", "show", f"v0.1.1:{path}"], cwd=ROOT, check=True, stdout=subprocess.PIPE).stdout
    return hashlib.sha256(content).hexdigest()


def test_v011_freeze_matches_realtime_artifacts():
    freeze = json.loads((ROOT / "profiles/v0.1.1.freeze.json").read_text(encoding="utf-8"))
    assert freeze["predecessor"] == "v0.1.0"
    assert freeze["codex_telemetry"]["context_estimation"] is False
    assert freeze["sha256"] == {
        "candidate_worker": digest("bin/codex-local-worker-v4"),
        "dashboard": digest("src/codex_local_harness/dashboard.py"),
        "workflow": digest("src/codex_local_harness/workflow.py"),
        "frontier": digest("src/codex_local_harness/frontier.py"),
        "observe_cli": digest("src/codex_local_harness/observe_cli.py"),
        "replay_workflow": digest("qualification/v011/replay-workflow.jsonl"),
        "replay_frontier": digest("qualification/v011/replay-frontier.jsonl"),
    }


def test_v011_version_and_baseline_nonreplacement():
    assert 'version = "0.3.0"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    tagged = subprocess.run(["git", "show", "v0.1.1:pyproject.toml"], cwd=ROOT, check=True, text=True, stdout=subprocess.PIPE).stdout
    assert 'version = "0.1.1"' in tagged
    freeze = json.loads((ROOT / "profiles/v0.1.1.freeze.json").read_text(encoding="utf-8"))
    assert freeze["shadow_install"]["baseline_replaced"] is False
