import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v051_freeze_binds_live_source_hotfix():
    freeze = json.loads((ROOT / "profiles/v0.5.1.freeze.json").read_text(encoding="utf-8"))
    assert (freeze["release"], freeze["predecessor"]) == ("v0.5.1", "v0.5.0")
    assert freeze["scope"] == "live-source-routing-hotfix"
    assert freeze["sha256"] == {
        "dashboard": "f8b707f717a1a4d6c971fd7051e54563ff7b904b04a187ade496306938ff1af5",
        "live_execution": "63841aaceab76d579018540b2ba252a035132be0d7738d789c154d90a9064872",
        "strategy": "720a7ba56f7eaa143af1670e9005533e36814206184f6275ab54faf1157214e8",
        "dashboard_unit": "e03fd5ed964b1b3d0fdfaf2af89b279107aac2c8f11508368cc745181cbdd18c",
        "qualified_routes": "76f4f5b6ba50fada9be0afdf4aebd588129f56166f26598127888de9bc8d4213",
    }
