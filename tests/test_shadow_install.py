import json
import hashlib
import os
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_shadow_installer_preserves_baseline_and_creates_private_backup(tmp_path):
    install_bin = tmp_path / "bin"
    backup_root = tmp_path / "backups"
    config = tmp_path / "config"
    install_bin.mkdir()
    config.mkdir()
    baseline = install_bin / "codex-local-worker"
    shutil.copy2(ROOT / "bin/codex-local-worker", baseline)
    workers = config / "workers.json"
    opencode = config / "opencode.json"
    workers.write_text('{"routes":{},"defaults":{}}\n', encoding="utf-8")
    opencode.write_text('{"permission":{}}\n', encoding="utf-8")
    before = baseline.read_bytes()
    environment = os.environ | {
        "CODEX_LOCAL_INSTALL_BIN": str(install_bin),
        "CODEX_LOCAL_BACKUP_ROOT": str(backup_root),
        "CODEX_LOCAL_BASELINE_WORKER": str(baseline),
        "CODEX_LOCAL_WORKERS_CONFIG": str(workers),
        "CODEX_LOCAL_OPENCODE_CONFIG": str(opencode),
    }
    completed = subprocess.run(
        [str(ROOT / "bin/install-shadow-v4")],
        check=True,
        text=True,
        capture_output=True,
        env=environment,
    )
    assert baseline.read_bytes() == before
    for name in ("worker", "helper", "coder", "reviewer"):
        assert (install_bin / f"codex-local-{name}-v4").stat().st_mode & 0o111
    backup = Path(next(line.split("=", 1)[1] for line in completed.stdout.splitlines() if line.startswith("backup=")))
    assert backup.parent == backup_root
    assert backup.stat().st_mode & 0o077 == 0
    manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["baseline_worker_sha256"] == "37ff3c637f71105fd14f3a700d2e7a95d0a680851ba4d502c5f05a076eabd415"


def test_v4_wrappers_have_valid_shell_syntax():
    for path in (ROOT / "bin").glob("codex-local-*-v4"):
        subprocess.run(["bash", "-n", str(path)], check=True)


def test_promotion_backs_up_baseline_and_is_idempotent(tmp_path):
    install_bin = tmp_path / "bin"
    backup_root = tmp_path / "backups"
    install_bin.mkdir()
    stable = install_bin / "codex-local-worker"
    shutil.copy2(ROOT / "bin/codex-local-worker", stable)
    candidate = ROOT / "bin/codex-local-worker-v4"
    manifest = tmp_path / "release.json"
    manifest.write_text(json.dumps({"qualified_worker_sha256": [
        hashlib.sha256(stable.read_bytes()).hexdigest(),
        hashlib.sha256(candidate.read_bytes()).hexdigest(),
    ]}), encoding="utf-8")
    environment = os.environ | {
        "CODEX_LOCAL_INSTALL_BIN": str(install_bin),
        "CODEX_LOCAL_BACKUP_ROOT": str(backup_root),
        "CODEX_LOCAL_STABLE_WORKER": str(stable),
        "CODEX_LOCAL_CANDIDATE_WORKER": str(candidate),
        "CODEX_LOCAL_CANDIDATE_SOURCE": str(ROOT / "src"),
        "CODEX_LOCAL_RUNTIME_ROOT": str(tmp_path / "runtime"),
        "CODEX_LOCAL_RELEASE_MANIFEST": str(manifest),
    }
    first = subprocess.run(
        [str(ROOT / "bin/promote-v4-worker")],
        check=True,
        text=True,
        capture_output=True,
        env=environment,
    )
    backup = Path(next(line.split("=", 1)[1] for line in first.stdout.splitlines() if line.startswith("backup=")))
    assert (backup / "codex-local-worker").read_bytes() == (ROOT / "bin/codex-local-worker").read_bytes()
    assert stable.read_bytes() == (ROOT / "bin/codex-local-worker-v4").read_bytes()
    assert (install_bin / "codex-local-model").read_bytes() == (ROOT / "bin/codex-local-model").read_bytes()
    assert json.loads((install_bin / "workers.json").read_text(encoding="utf-8"))["routes"]["coder/complex"]["model"] == "vllm/Qwen/Qwen3-Coder-Next-FP8"
    assert (backup / "codex-local-model.absent").is_file()
    assert (backup / "workers.json.absent").is_file()
    runtime = Path(next(line.split("=", 1)[1] for line in first.stdout.splitlines() if line.startswith("runtime=")))
    assert (runtime / "src/codex_local_harness").is_dir()
    assert list(runtime.rglob("*.py"))
    assert not list(runtime.rglob("*.pyc"))
    assert not list(runtime.rglob("__pycache__"))
    second = subprocess.run(
        [str(ROOT / "bin/promote-v4-worker")],
        check=True,
        text=True,
        capture_output=True,
        env=environment,
    )
    assert "status=runtime-refreshed" in second.stdout
    rolled_back = subprocess.run(
        [str(ROOT / "bin/rollback-v4-worker"), str(backup)],
        check=True,
        text=True,
        capture_output=True,
        env=environment,
    )
    assert "status=rolled-back" in rolled_back.stdout
    assert stable.read_bytes() == (ROOT / "bin/codex-local-worker").read_bytes()
    assert not (install_bin / "codex-local-model").exists()
    assert not (install_bin / "workers.json").exists()


def test_promotion_rejects_empty_python_package_before_backup(tmp_path):
    install_bin = tmp_path / "bin"
    backup_root = tmp_path / "backups"
    source = tmp_path / "src/codex_local_harness"
    install_bin.mkdir()
    source.mkdir(parents=True)
    stable = install_bin / "codex-local-worker"
    shutil.copy2(ROOT / "bin/codex-local-worker", stable)
    completed = subprocess.run(
        [str(ROOT / "bin/promote-v4-worker")],
        text=True,
        capture_output=True,
        env=os.environ | {
            "CODEX_LOCAL_INSTALL_BIN": str(install_bin),
            "CODEX_LOCAL_BACKUP_ROOT": str(backup_root),
            "CODEX_LOCAL_STABLE_WORKER": str(stable),
            "CODEX_LOCAL_CANDIDATE_WORKER": str(ROOT / "bin/codex-local-worker-v4"),
            "CODEX_LOCAL_CANDIDATE_SOURCE": str(tmp_path / "src"),
            "CODEX_LOCAL_RUNTIME_ROOT": str(tmp_path / "runtime"),
        },
    )
    assert completed.returncode == 2
    assert "contains no modules" in completed.stderr
    assert not backup_root.exists()


def test_promotion_rejects_worker_not_in_release_manifest(tmp_path):
    install_bin = tmp_path / "bin"
    install_bin.mkdir()
    stable = install_bin / "codex-local-worker"
    candidate = tmp_path / "candidate"
    stable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    candidate.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stable.chmod(0o755)
    candidate.chmod(0o755)
    source = tmp_path / "src/codex_local_harness"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("", encoding="utf-8")
    manifest = tmp_path / "release.json"
    manifest.write_text('{"qualified_worker_sha256": []}\n', encoding="utf-8")

    completed = subprocess.run(
        [str(ROOT / "bin/promote-v4-worker")], text=True, capture_output=True,
        env=os.environ | {
            "CODEX_LOCAL_INSTALL_BIN": str(install_bin),
            "CODEX_LOCAL_STABLE_WORKER": str(stable),
            "CODEX_LOCAL_CANDIDATE_WORKER": str(candidate),
            "CODEX_LOCAL_CANDIDATE_SOURCE": str(tmp_path / "src"),
            "CODEX_LOCAL_RUNTIME_ROOT": str(tmp_path / "runtime"),
            "CODEX_LOCAL_BACKUP_ROOT": str(tmp_path / "backups"),
            "CODEX_LOCAL_RELEASE_MANIFEST": str(manifest),
        },
    )

    assert completed.returncode == 65
    assert "stable worker is not a qualified release" in completed.stderr
    assert not (tmp_path / "backups").exists()


def test_promotion_installs_dashboard_unit_and_rollback_restores_it(tmp_path):
    install_bin = tmp_path / "bin"
    unit_dir = tmp_path / "units"
    install_bin.mkdir()
    unit_dir.mkdir()
    stable = install_bin / "codex-local-worker"
    shutil.copy2(ROOT / "bin/codex-local-worker", stable)
    candidate = ROOT / "bin/codex-local-worker-v4"
    manifest = tmp_path / "release.json"
    manifest.write_text(json.dumps({"qualified_worker_sha256": [
        hashlib.sha256(stable.read_bytes()).hexdigest(),
        hashlib.sha256(candidate.read_bytes()).hexdigest(),
    ]}), encoding="utf-8")
    installed_unit = unit_dir / "dashboard-backend.service"
    installed_unit.write_text("old-unit\n", encoding="utf-8")
    fake_systemctl = tmp_path / "systemctl"
    calls = tmp_path / "systemctl.calls"
    fake_systemctl.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$*\" >> {calls}\n", encoding="utf-8")
    fake_systemctl.chmod(0o755)
    environment = os.environ | {
        "CODEX_LOCAL_INSTALL_BIN": str(install_bin),
        "CODEX_LOCAL_BACKUP_ROOT": str(tmp_path / "backups"),
        "CODEX_LOCAL_STABLE_WORKER": str(stable),
        "CODEX_LOCAL_CANDIDATE_WORKER": str(candidate),
        "CODEX_LOCAL_CANDIDATE_SOURCE": str(ROOT / "src"),
        "CODEX_LOCAL_RUNTIME_ROOT": str(tmp_path / "runtime"),
        "CODEX_LOCAL_RELEASE_MANIFEST": str(manifest),
        "CODEX_LOCAL_INSTALL_UNITS": "1",
        "CODEX_LOCAL_DASHBOARD_UNIT": str(ROOT / "deployment/dashboard-backend.service"),
        "CODEX_LOCAL_USER_UNIT_DIR": str(unit_dir),
        "CODEX_LOCAL_SYSTEMCTL": str(fake_systemctl),
    }
    promoted = subprocess.run([str(ROOT / "bin/promote-v4-worker")], check=True, text=True, capture_output=True, env=environment)
    backup = Path(next(line.split("=", 1)[1] for line in promoted.stdout.splitlines() if line.startswith("backup=")))
    assert installed_unit.read_bytes() == (ROOT / "deployment/dashboard-backend.service").read_bytes()
    assert (backup / "dashboard-backend.service").read_text(encoding="utf-8") == "old-unit\n"
    subprocess.run([str(ROOT / "bin/rollback-v4-worker"), str(backup)], check=True, env=environment)
    assert installed_unit.read_text(encoding="utf-8") == "old-unit\n"
    assert calls.read_text(encoding="utf-8").splitlines() == [
        "--user daemon-reload", "--user try-restart dashboard-backend.service",
        "--user daemon-reload", "--user try-restart dashboard-backend.service",
    ]
