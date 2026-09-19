from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin/codex-local-deepseek-v4"


def _runtime(tmp_path: Path, *, production_active: bool, available_kib: int = 100_000_000):
    recipe = tmp_path / "recipe"
    recipe.mkdir()
    (recipe / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    (recipe / "compose.codex.yaml").write_text("services: {}\n", encoding="utf-8")
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(f"MemAvailable: {available_kib} kB\n", encoding="utf-8")
    calls = tmp_path / "docker.calls"
    docker = tmp_path / "docker"
    docker.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >>"$FAKE_DOCKER_CALLS"\n'
        'case "$*" in "image inspect"*) exit 0;; esac\n'
        "exit 0\n",
        encoding="utf-8",
    )
    curl = tmp_path / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        + ("case \"$*\" in *127.0.0.1:8000/health*) exit 0;; esac\n" if production_active else "")
        + "exit 22\n",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    curl.chmod(0o755)
    env = os.environ | {
        "CODEX_LOCAL_DEEPSEEK_RECIPE_DIR": str(recipe),
        "CODEX_LOCAL_DOCKER": str(docker),
        "CODEX_LOCAL_CURL": str(curl),
        "CODEX_LOCAL_MEMINFO": str(meminfo),
        "FAKE_DOCKER_CALLS": str(calls),
    }
    return env, calls


def test_start_refuses_while_production_is_active(tmp_path: Path) -> None:
    env, calls = _runtime(tmp_path, production_active=True)
    completed = subprocess.run([SCRIPT, "start"], env=env, text=True, capture_output=True)
    assert completed.returncode == 75
    assert "production vLLM is active" in completed.stderr
    assert "compose" not in calls.read_text(encoding="utf-8")


def test_start_refuses_under_memory_floor(tmp_path: Path) -> None:
    env, calls = _runtime(tmp_path, production_active=False, available_kib=1)
    completed = subprocess.run([SCRIPT, "start"], env=env, text=True, capture_output=True)
    assert completed.returncode == 75
    assert "require at least" in completed.stderr
    assert "compose" not in calls.read_text(encoding="utf-8")


def test_start_uses_pinned_image_isolated_port_and_overlay(tmp_path: Path) -> None:
    env, calls = _runtime(tmp_path, production_active=False)
    completed = subprocess.run([SCRIPT, "start"], env=env, text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr
    docker_calls = calls.read_text(encoding="utf-8")
    assert "sha256:86f99e42f2061322e7bc47691e09d9ebbef22951ec8aebd7662f3d1da1a535bc" in docker_calls
    assert "compose --project-name codex-deepseek-v4-candidate" in docker_calls
    assert "compose.codex.yaml -f " in docker_calls
    assert "deepseek-v4-reasoning.yaml up -d" in docker_calls


def test_managed_overlay_binds_localhost_candidate_port() -> None:
    overlay = (ROOT / "config/deepseek-v4-compose.codex.yaml").read_text(encoding="utf-8")
    assert "HOST: 127.0.0.1" in overlay
    assert "PORT: 8011" in overlay
    assert 'restart: "no"' in overlay
