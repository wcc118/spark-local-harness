import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v056_freeze_binds_operator_console():
    freeze = json.loads((ROOT / "profiles/v0.5.6.freeze.json").read_text(encoding="utf-8"))
    assert (freeze["release"], freeze["predecessor"]) == ("v0.5.6", "v0.5.5")
    assert freeze["sha256"] == {
        "control_plane": "c979548afb574530bd5e73b5a7dd0cd1844ac4bd36e9dde4703cbd5cb52e8d5e",
        "dashboard": "7d949ce4ea3754b65d1fb6320508189b37ace3f35bd87e70a2480edacf298803",
        "bridge": "5a618bd4b90223933683e63a7d9739ee01c3f5f6c23ea8d48e35b80d8d5c2141",
        "dashboard_unit": "29db1be90be9179cc06a9f23e6920d256a7ec0ae38ba7792db023329289b4b17",
        "promoter": "496bd039e5b0de9629925c3d8cef7186646b82204809274334b1f487dcf6f0e6"
    }
