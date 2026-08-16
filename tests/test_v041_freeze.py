import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

def test_v041_freeze_preserves_historical_favicon_patch():
    freeze = json.loads((ROOT / "profiles/v0.4.1.freeze.json").read_text(encoding="utf-8"))
    assert (freeze["release"], freeze["predecessor"]) == ("v0.4.1", "v0.4.0")
    assert freeze["scope"] == "dashboard-favicon-only"
    assert freeze["sha256"] == {
        "dashboard": "f21b335c4f8d99d01216abfbdb90f67d9745c340cb8fcaf0fb76e2fae2840fa1",
        "dashboard_unit": "71a2106672c56924820c375d8120c43781cb5660308561ac3cb5289896a0f075",
    }
