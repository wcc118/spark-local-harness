import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _digest(relative: str) -> str:
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def test_v030_freeze_binds_candidate_components_and_preserves_predecessor():
    freeze = json.loads((ROOT / "profiles/v0.3.0.freeze.json").read_text(encoding="utf-8"))
    assert (freeze["release"], freeze["predecessor"]) == ("v0.3.0", "v0.2.0")
    assert freeze["sha256"] == {
        "workflow": _digest("src/codex_local_harness/workflow.py"),
        "dashboard": _digest("src/codex_local_harness/dashboard.py"),
        "observe_cli": _digest("src/codex_local_harness/observe_cli.py"),
        "replay_workflow": _digest("qualification/v030/replay-workflow.jsonl"),
        "worker": _digest("bin/codex-local-worker-v4"),
    }
    installed = json.loads((ROOT / "config/installed-release.json").read_text(encoding="utf-8"))
    assert freeze["sha256"]["worker"] in installed["qualified_worker_sha256"]
    assert (ROOT / "docs/releases/v0.2.0.md").exists()
