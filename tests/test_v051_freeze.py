import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def digest(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def test_v051_freeze_binds_live_source_hotfix():
    freeze = json.loads((ROOT / "profiles/v0.5.1.freeze.json").read_text(encoding="utf-8"))
    assert (freeze["release"], freeze["predecessor"]) == ("v0.5.1", "v0.5.0")
    assert freeze["scope"] == "live-source-routing-hotfix"
    assert freeze["sha256"] == {
        "dashboard": digest("src/codex_local_harness/dashboard.py"),
        "live_execution": digest("src/codex_local_harness/live_execution.py"),
        "strategy": digest("src/codex_local_harness/strategy.py"),
        "dashboard_unit": digest("deployment/dashboard-backend.service"),
        "qualified_routes": digest("config/qualified-qwen38-routes.json"),
    }
