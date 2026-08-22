import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def digest(path: str) -> str:
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def test_v055_freeze_binds_dual_model_runtime():
    freeze = json.loads((ROOT / "profiles/v0.5.5.freeze.json").read_text(encoding="utf-8"))
    assert (freeze["release"], freeze["predecessor"]) == ("v0.5.5", "v0.5.4")
    assert freeze["sha256"] == {
        "worker": digest("bin/codex-local-worker-v4"),
        "model_manager": digest("bin/codex-local-model"),
        "routes": digest("config/workers-vllm.json"),
        "promoter": digest("bin/promote-v4-worker"),
        "rollback": digest("bin/rollback-v4-worker"),
    }
