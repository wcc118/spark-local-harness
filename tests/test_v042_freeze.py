import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v042_freeze_preserves_historical_dashboard_plan_layout_repair():
    freeze = json.loads((ROOT / "profiles/v0.4.2.freeze.json").read_text(encoding="utf-8"))
    assert (freeze["release"], freeze["predecessor"]) == ("v0.4.2", "v0.4.1")
    assert freeze["scope"] == "dashboard-plan-layout-repair"
    assert freeze["governance"]["dashboard_authority"] == "read-only"
    assert freeze["sha256"] == {
        "dashboard": "1205bdf272115f512a362af3b256cfaaba3e6faef2c6c86d99ea1d71aee32c14",
        "dashboard_unit": "3a3d0d382f9b8063edc00f7435132ee0561c61a3002536d1f1ffe9682e2952b3",
    }
