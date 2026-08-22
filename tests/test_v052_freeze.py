import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v052_freeze_binds_truthful_worker_release():
    freeze = json.loads((ROOT / "profiles/v0.5.2.freeze.json").read_text(encoding="utf-8"))
    assert (freeze["release"], freeze["predecessor"]) == ("v0.5.2", "v0.5.1")
    assert freeze["scope"] == "truthful-worker-permissions-and-activity"
    assert freeze["sha256"] == {
        "worker": "9386218d4f15a16b1ef5a69a4020260888f7fdaaca147337dac88b82c4f45cf1",
        "dashboard": "45d2ac76583474a4c5f544d834a0c357bfacc0421417590adfb990cb24852409",
        "live_execution": "c179873e236554a2812f932edff291d18592d9c9337faab37f6789c7b4bd3f02",
        "dashboard_unit": "acc974b770a050e79ef13afd46354dcd31d6b4bd675428928f4dc538873558ee",
        "installed_release": "b1efc66c18cb76d27f960844430c6d9b1a054d420b339aceb5548e40ca75e532",
    }
