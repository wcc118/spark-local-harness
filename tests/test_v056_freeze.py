import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def digest(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def test_v056_freeze_binds_operator_console():
    freeze = json.loads((ROOT / "profiles/v0.5.6.freeze.json").read_text(encoding="utf-8"))
    assert (freeze["release"], freeze["predecessor"]) == ("v0.5.6", "v0.5.5")
    assert freeze["sha256"] == {
        "control_plane": digest("src/codex_local_harness/control_plane.py"),
        "dashboard": digest("src/codex_local_harness/dashboard.py"),
        "bridge": digest("src/codex_local_harness/opencode_bridge.py"),
        "dashboard_unit": digest("deployment/dashboard-backend.service"),
        "promoter": digest("bin/promote-v4-worker"),
    }
