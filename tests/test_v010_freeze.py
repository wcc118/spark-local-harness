import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def digest(relative):
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def test_v010_freeze_matches_release_artifacts():
    freeze = json.loads((ROOT / "profiles/v0.1.0.freeze.json").read_text(encoding="utf-8"))
    assert freeze["release"] == "v0.1.0"
    assert freeze["governance"]["merge_authority"] == "codex"
    assert freeze["governance"]["dashboard_authority"] == "read-only"
    assert freeze["sha256"] == {
        "candidate_worker": digest("bin/codex-local-worker-v4"),
        "replay_runs": digest("qualification/v010/replay-runs.jsonl"),
        "replay_adjudications": digest("qualification/v010/replay-adjudications.jsonl"),
        "replay_lifecycle": digest("qualification/v010/replay-lifecycle.jsonl"),
    }


def test_project_version_is_first_scaffold_release():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.1.0"' in pyproject
