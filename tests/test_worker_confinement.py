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
        "CODEX_LOCAL_SESSION_BRIDGE": "0",
        "CODEX_LOCAL_MODEL_SWITCH": "0",
    }

    completed = subprocess.run(
        [str(WORKER), "helper", str(repo), "attempt external write"],
        env=env, text=True, capture_output=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "confined" in completed.stdout
    assert protected.read_text(encoding="utf-8") == "original\n"


def test_generated_worker_policy_denies_external_paths_without_blocking_sandboxed_shell():
    content = WORKER.read_text(encoding="utf-8")
    assert '.permission.external_directory = "deny"' in content
    assert '"external_directory": "deny"' in content
    assert '"*": "deny",\n          "git status*"' not in content
    assert 'repository_mount="type=bind,src=$worker_repo_path,dst=$worker_repo_path,readonly"' in content
    assert 'git clone --quiet --no-hardlinks --no-checkout "$worker_source_repo_path" "$worker_repo_path"' in content
    assert 'checkout_kind=isolated-clone' in content
    assert 'CODEX_LOCAL_CODER_POSTFLIGHT:-1' in content
    assert 'isolated coder requires CODEX_LOCAL_REPOSITORY and CODEX_LOCAL_ISSUE' in content


def test_interrupted_coder_mutates_only_disposable_clone(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "issue-79-test", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    (repo / "value.txt").write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "value.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/wcc118/example.git"], check=True)
    before_head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()

    fake = tmp_path / "fake-opencode"
    fake.write_text(
        "#!/bin/sh\n"
        "while [ $# -gt 0 ]; do [ \"$1\" = --dir ] && { shift; target=$1; }; shift; done\n"
        "printf 'isolated mutation\\n' > \"$target/value.txt\"\n"
        "printf '%s\\n' '{\"type\":\"text\",\"part\":{\"text\":\"interrupted partial work\"}}'\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    routes = tmp_path / "routes.json"
    routes.write_text(json.dumps({
        "defaults": {"coder": "complex"},
        "routes": {"coder/complex": {"agent": "coder", "model": "vllm/test"}},
    }), encoding="utf-8")
    isolation = tmp_path / "isolated"
    completed = subprocess.run(
        [str(WORKER), "coder", str(repo), "make a partial edit"],
        env=os.environ | {
            "CODEX_LOCAL_WORKER_CONFIG": str(routes),
            "CODEX_LOCAL_OPENCODE": str(fake),
            "CODEX_LOCAL_SOURCE": str(ROOT / "src"),
            "CODEX_LOCAL_SANDBOX": "disabled",
            "CODEX_LOCAL_ALLOW_UNSANDBOXED": "1",
            "CODEX_LOCAL_CODER_POSTFLIGHT": "0",
            "CODEX_LOCAL_MODEL_SWITCH": "0",
            "CODEX_LOCAL_REPOSITORY": "wcc118/example",
            "CODEX_LOCAL_ISSUE": "79",
            "CODEX_LOCAL_ISOLATION_PARENT": str(isolation),
        },
        text=True, capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert (repo / "value.txt").read_text(encoding="utf-8") == "original\n"
    assert subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip() == before_head
    assert subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain=v1"], text=True) == ""
    assert isolation.is_dir() and list(isolation.iterdir()) == []
