import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def digest(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def test_v052_freeze_binds_truthful_worker_release():
    freeze = json.loads((ROOT / "profiles/v0.5.2.freeze.json").read_text(encoding="utf-8"))
    assert (freeze["release"], freeze["predecessor"]) == ("v0.5.2", "v0.5.1")
    assert freeze["scope"] == "truthful-worker-permissions-and-activity"
    assert freeze["sha256"] == {
        "worker": digest("bin/codex-local-worker-v4"),
        "dashboard": digest("src/codex_local_harness/dashboard.py"),
        "live_execution": digest("src/codex_local_harness/live_execution.py"),
        "dashboard_unit": digest("deployment/dashboard-backend.service"),
        "installed_release": digest("config/installed-release.json"),
    }
    release = json.loads((ROOT / "config/installed-release.json").read_text(encoding="utf-8"))
    assert freeze["sha256"]["worker"] in release["qualified_worker_sha256"]
