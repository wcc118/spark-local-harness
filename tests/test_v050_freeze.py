import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v050_freeze_binds_live_agent_brain_release():
    freeze = json.loads((ROOT / "profiles/v0.5.0.freeze.json").read_text(encoding="utf-8"))
    assert (freeze["release"], freeze["predecessor"]) == ("v0.5.0", "v0.4.4")
    assert freeze["scope"] == "live-agent-brain-observatory"
    assert freeze["governance"] == {
        "orchestrator": "codex",
        "dashboard_authority": "read-only",
        "private_reasoning_capture": False,
        "automatic_harness_tuning": False,
        "synthetic_model_score": False,
    }
    assert freeze["sha256"] == {
        "dashboard": "d62592e815ed25bc118971c1f84f910da2b86ba81784f8345a4dfaa31451a9aa",
        "live_execution": "f63415f77062d6a7d1993f2b3d19c06e69184f576032a7457da93ceb784224ee",
        "strategy": "720a7ba56f7eaa143af1670e9005533e36814206184f6275ab54faf1157214e8",
        "dashboard_unit": "3b51bc0608200cd81492c577bb2ddf59f3423702066563c8c032aaeae8eeaf9d",
        "qualified_routes": "76f4f5b6ba50fada9be0afdf4aebd588129f56166f26598127888de9bc8d4213",
    }
