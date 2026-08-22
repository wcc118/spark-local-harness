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
        "dashboard": "4ad6076e181a5a9f7dc50fd5b57857e7764fa71089209c0383de1d2a3df58701",
        "dashboard_unit": "497c5eeaa03c38230b0c4d464c86bec1dc2263dcdbcfc6a85fe93c42c7cfc013",
        "claude_usage": digest("src/codex_local_harness/claude_usage.py"),
    }
