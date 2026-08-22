import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v053_freeze_binds_opencode_session_bridge():
    freeze = json.loads((ROOT / "profiles/v0.5.3.freeze.json").read_text(encoding="utf-8"))
    assert (freeze["release"], freeze["predecessor"]) == ("v0.5.3", "v0.5.2")
    assert freeze["scope"] == "opencode-session-bridge"
    assert freeze["sha256"] == {
        "worker": "d712d5dd0654394aa54edb6e07a212127e1a643726945762fe128264f150d908",
        "bridge": "54c5bc9269933072e46d844ae6ef35db968a452d0d8ef31688b8d398a7f89e1e",
        "live_execution": "f93038d58d1c214f1fd9df0bf8df2359453fd56b9efe060dea6734a3d331e1f7",
        "dashboard_unit": "e79ed6c76fc0645a3bde597f9723151332bce0d1fcf598773e5815f2fc597f01",
        "installed_release": "fdd5be6c89ad71db51ae8f1653b59cd478f07303e16be2dcb2d42262f51ffda5",
    }
