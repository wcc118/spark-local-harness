import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v043_freeze_preserves_historical_dashboard_triage_and_signal_legibility():
    freeze = json.loads((ROOT / "profiles/v0.4.3.freeze.json").read_text(encoding="utf-8"))
    assert (freeze["release"], freeze["predecessor"]) == ("v0.4.3", "v0.4.2")
    assert freeze["scope"] == "dashboard-triage-and-signal-legibility"
    assert freeze["governance"]["dashboard_authority"] == "read-only"
    assert freeze["sha256"] == {
        "dashboard": "f11ec15713e1f8e97df3bc2bcd839d73d7a58a3cc9c2091b2251f05688c44040",
        "dashboard_unit": "4ca9cfca8189d531e1283e3704909a6fcb2a9a8214a29d1509c4bc85bbc26ddc",
    }
