import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def digest(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def test_v044_freeze_binds_claude_operation_usage_capture():
    freeze = json.loads((ROOT / "profiles/v0.4.4.freeze.json").read_text(encoding="utf-8"))
    assert (freeze["release"], freeze["predecessor"]) == ("v0.4.4", "v0.4.3")
    assert freeze["scope"] == "claude-operation-usage-capture"
    assert freeze["governance"]["dashboard_authority"] == "read-only"
    assert freeze["sha256"] == {
        "dashboard": digest("src/codex_local_harness/dashboard.py"),
        "dashboard_unit": digest("deployment/dashboard-backend.service"),
        "claude_usage": digest("src/codex_local_harness/claude_usage.py"),
    }
