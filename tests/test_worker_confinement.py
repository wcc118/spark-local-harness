from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "bin" / "codex-local-worker-v4"


def _routes(path: Path) -> Path:
    path.write_text(json.dumps({
        "defaults": {"helper": "standard"},
        "routes": {"helper/standard": {"agent": "helper", "model": "vllm/test"}},
    }), encoding="utf-8")
    return path


def test_container_worker_cannot_write_outside_checkout(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    protected = tmp_path / "protected"
    protected.write_text("original\n", encoding="utf-8")
    fake = tmp_path / "fake-opencode"
    fake.write_text(
        "#!/bin/sh\n"
        f"printf 'tampered\\n' > {protected} 2>/dev/null || true\n"
        "printf '%s\\n' '{\"type\":\"text\",\"part\":{\"text\":\"confined\"}}'\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = os.environ | {
        "CODEX_LOCAL_WORKER_CONFIG": str(_routes(tmp_path / "routes.json")),
        "CODEX_LOCAL_OPENCODE": str(fake),
        "CODEX_LOCAL_SOURCE": str(ROOT / "src"),
        "CODEX_LOCAL_CONTAINER_IMAGE": "codex-local-worker:0.1.0",
    }

    completed = subprocess.run(
        [str(WORKER), "helper", str(repo), "attempt external write"],
        env=env, text=True, capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "confined" in completed.stdout
    assert protected.read_text(encoding="utf-8") == "original\n"


def test_generated_worker_policy_denies_external_paths_and_shell_by_default():
    content = WORKER.read_text(encoding="utf-8")
    assert '.permission.external_directory = "deny"' in content
    assert '"external_directory": "deny"' in content
    assert '"*": "deny"' in content
    assert 'repository_mount="type=bind,src=$worker_repo_path,dst=$worker_repo_path,readonly"' in content
