import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def digest(relative: str) -> str:
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def test_v040_freeze_binds_public_plan_candidate():
    freeze = json.loads((ROOT / "profiles/v0.4.0.freeze.json").read_text(encoding="utf-8"))
    assert (freeze["release"], freeze["predecessor"]) == ("v0.4.0", "v0.3.0")
    assert freeze["governance"]["private_reasoning_capture"] is False
    assert freeze["sha256"]["plan_event"] == digest("src/codex_local_harness/plan_event.py")
    assert freeze["sha256"]["plan_cli"] == digest("src/codex_local_harness/plan_cli.py")
    assert freeze["sha256"]["replay_plans"] == digest("qualification/v040/replay-plans.jsonl")
    assert all(len(value) == 64 for value in freeze["sha256"].values())
