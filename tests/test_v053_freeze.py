import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def digest(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def test_v053_freeze_binds_opencode_session_bridge():
    freeze = json.loads((ROOT / "profiles/v0.5.3.freeze.json").read_text(encoding="utf-8"))
    assert (freeze["release"], freeze["predecessor"]) == ("v0.5.3", "v0.5.2")
    assert freeze["scope"] == "opencode-session-bridge"
    assert freeze["sha256"] == {
        "worker": digest("bin/codex-local-worker-v4"),
        "bridge": digest("src/codex_local_harness/opencode_bridge.py"),
        "live_execution": digest("src/codex_local_harness/live_execution.py"),
        "dashboard_unit": digest("deployment/dashboard-backend.service"),
        "installed_release": digest("config/installed-release.json"),
    }
    release = json.loads((ROOT / "config/installed-release.json").read_text(encoding="utf-8"))
    assert freeze["sha256"]["worker"] in release["qualified_worker_sha256"]
