import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_production_freeze_binds_codex_governance_and_qualified_worker():
    freeze = json.loads((ROOT / "profiles/codex-governed-production.freeze.json").read_text())
    release = json.loads((ROOT / "config/installed-release.json").read_text())
    worker_digest = digest(ROOT / "bin/codex-local-worker-v4")
    assert freeze["governance"]["primary_orchestrator"] == "codex"
    assert freeze["governance"]["merge_authority"] == "codex"
    assert freeze["governance"]["local_reviewer_approval_authorizes_merge"] is False
    assert freeze["sha256"]["worker"] == worker_digest
    assert worker_digest in release["qualified_worker_sha256"]
