import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def digest(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def test_v054_freeze_binds_bridged_tool_accounting():
    freeze = json.loads((ROOT / "profiles/v0.5.4.freeze.json").read_text(encoding="utf-8"))
    assert (freeze["release"], freeze["predecessor"]) == ("v0.5.4", "v0.5.3")
    assert freeze["sha256"] == {
        "runtime_parser": digest("src/codex_local_harness/runtime/opencode.py"),
        "dashboard_unit": digest("deployment/dashboard-backend.service"),
        "worker": digest("bin/codex-local-worker-v4"),
        "bridge": digest("src/codex_local_harness/opencode_bridge.py"),
    }
