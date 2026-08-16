import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def digest(relative):
    content = subprocess.run(
        ["git", "show", f"v0.1.0:{relative}"], cwd=ROOT, check=True, stdout=subprocess.PIPE,
    ).stdout
    return hashlib.sha256(content).hexdigest()


def test_v010_freeze_matches_release_artifacts():
    freeze = json.loads((ROOT / "profiles/v0.1.0.freeze.json").read_text(encoding="utf-8"))
    assert freeze["release"] == "v0.1.0"
    assert freeze["governance"]["merge_authority"] == "codex"
    assert freeze["governance"]["dashboard_authority"] == "read-only"
    assert freeze["sha256"] == {
        "candidate_worker": digest("bin/codex-local-worker-v4"),
        "routing_profile": digest("profiles/codex-led-v4.json"),
        "dashboard": digest("src/codex_local_harness/dashboard.py"),
        "diagnostics": digest("src/codex_local_harness/diagnostics.py"),
        "read_model": digest("src/codex_local_harness/read_model.py"),
        "replay_runs": digest("qualification/v010/replay-runs.jsonl"),
        "replay_adjudications": digest("qualification/v010/replay-adjudications.jsonl"),
        "replay_lifecycle": digest("qualification/v010/replay-lifecycle.jsonl"),
    }


def test_project_version_advances_without_rewriting_v010_tag():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.2.0"' in pyproject
    tagged = subprocess.run(
        ["git", "show", "v0.1.0:pyproject.toml"], cwd=ROOT, check=True, text=True, stdout=subprocess.PIPE,
    ).stdout
    assert 'version = "0.1.0"' in tagged
