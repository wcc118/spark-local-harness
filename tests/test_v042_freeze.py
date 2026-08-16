import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def digest(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def test_v042_freeze_binds_dashboard_plan_layout_repair():
    freeze = json.loads((ROOT / "profiles/v0.4.2.freeze.json").read_text(encoding="utf-8"))
    assert (freeze["release"], freeze["predecessor"]) == ("v0.4.2", "v0.4.1")
    assert freeze["scope"] == "dashboard-plan-layout-repair"
    assert freeze["governance"]["dashboard_authority"] == "read-only"
    assert freeze["sha256"] == {
        "dashboard": digest("src/codex_local_harness/dashboard.py"),
        "dashboard_unit": digest("deployment/dashboard-backend.service"),
    }
