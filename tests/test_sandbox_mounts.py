from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from codex_local_harness.sandbox_mounts import MountConfigError, mount_arguments

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "bin" / "codex-local-worker-v4"


@pytest.fixture
def layout(tmp_path):
    home = tmp_path / "home"
    engines = home / ".local/share/flight-loads/engines"
    engines.mkdir(parents=True)
    worker_home = tmp_path / "worker-home"
    worker_home.mkdir()
    return home, engines, worker_home


def _config(source, target="~/.local/share/flight-loads/engines"):
    return {"schema": 1, "readonly_mounts": [{"source": str(source), "target": target}]}


def _mounts(config, home, worker_home):
    # tmp_path lives under /tmp, which the production denylist protects.
    return mount_arguments(config, home, worker_home, system_denied=("/", "/etc", "/proc"))


def test_valid_mount_is_readonly_and_targets_worker_data_dir(layout):
    home, engines, worker_home = layout
    assert _mounts(_config(engines), home, worker_home) == [
        f"type=bind,src={engines},dst={worker_home}/.local/share/flight-loads/engines,readonly"
    ]


def test_empty_mount_list_is_allowed(layout):
    home, _, worker_home = layout
    assert _mounts({"schema": 1, "readonly_mounts": []}, home, worker_home) == []


@pytest.mark.parametrize("relative", [
    ".ssh", ".config/gh", ".codex", ".claude", ".aws", ".docker",
    ".local/share/codex-local-harness/runtime", ".local/share/opencode-orchestration/worker-homes",
])
def test_credential_config_and_harness_sources_are_rejected(layout, relative):
    home, _, worker_home = layout
    source = home / relative
    source.mkdir(parents=True)
    with pytest.raises(MountConfigError, match="protected location"):
        _mounts(_config(source), home, worker_home)


@pytest.mark.parametrize("relative", ["", ".local", ".local/share"])
def test_home_and_ancestors_of_protected_paths_are_rejected(layout, relative):
    home, _, worker_home = layout
    source = home / relative if relative else home
    with pytest.raises(MountConfigError):
        _mounts(_config(source), home, worker_home)


def test_ancestor_of_home_is_rejected(layout):
    home, _, worker_home = layout
    with pytest.raises(MountConfigError, match="ancestor"):
        _mounts(_config(home.parent), home, worker_home)


def test_production_denylist_rejects_system_paths(layout):
    home, _, worker_home = layout
    for source in ("/etc", "/proc", "/tmp"):
        with pytest.raises(MountConfigError):
            mount_arguments(_config(source), home, worker_home)


def test_symlinked_source_is_rejected(layout):
    home, engines, worker_home = layout
    link = home / ".local/share/engines-link"
    link.symlink_to(engines)
    with pytest.raises(MountConfigError, match="symlinks"):
        _mounts(_config(link), home, worker_home)


@pytest.mark.parametrize("source", ["relative/path", "/nonexistent/engine/dir"])
def test_relative_or_missing_source_is_rejected(layout, source):
    home, _, worker_home = layout
    with pytest.raises(MountConfigError):
        _mounts(_config(source), home, worker_home)


def test_file_source_is_rejected(layout):
    home, engines, worker_home = layout
    binary = engines / "xfoil"
    binary.write_text("binary")
    with pytest.raises(MountConfigError, match="not an existing directory"):
        _mounts(_config(binary), home, worker_home)


@pytest.mark.parametrize("target", [
    "/usr/local/bin", "~/.config/opencode", "~/.cache/x", "~/.local/share", "~/../escape",
    "~/.local/share/../../.config", "relative", "~/.local/share/a,dst=/etc",
])
def test_targets_outside_worker_data_dir_are_rejected(layout, target):
    home, engines, worker_home = layout
    with pytest.raises(MountConfigError):
        _mounts(_config(engines, target), home, worker_home)


def test_overlapping_targets_are_rejected(layout):
    home, engines, worker_home = layout
    other = home / ".local/share/other"
    other.mkdir()
    config = {"schema": 1, "readonly_mounts": [
        {"source": str(engines), "target": "~/.local/share/tools"},
        {"source": str(other), "target": "~/.local/share/tools/nested"},
    ]}
    with pytest.raises(MountConfigError, match="overlaps another mount"):
        _mounts(config, home, worker_home)


@pytest.mark.parametrize("config", [
    {"schema": 2, "readonly_mounts": []},
    {"schema": True, "readonly_mounts": []},
    {"schema": 1},
    {"schema": 1, "readonly_mounts": {}},
    {"schema": 1, "readonly_mounts": [{"source": "/x"}]},
    {"schema": 1, "readonly_mounts": [], "extra": 1},
])
def test_malformed_configuration_is_rejected(layout, config):
    home, _, worker_home = layout
    with pytest.raises(MountConfigError):
        _mounts(config, home, worker_home)


def test_cli_rejects_invalid_configuration_with_exit_2(tmp_path):
    config = tmp_path / "mounts.json"
    config.write_text(json.dumps({"schema": 1, "readonly_mounts": [{"source": "/etc", "target": "~/.local/share/etc"}]}))
    completed = subprocess.run(
        [sys.executable, "-m", "codex_local_harness.sandbox_mounts", "--config", str(config),
         "--home", str(tmp_path), "--worker-home", str(tmp_path / "w")],
        env=os.environ | {"PYTHONPATH": str(ROOT / "src")}, text=True, capture_output=True,
    )
    assert completed.returncode == 2
    assert "sandbox mount configuration rejected" in completed.stderr
    assert completed.stdout == ""


def test_worker_applies_validated_mounts_before_any_container_run():
    content = WORKER.read_text(encoding="utf-8")
    hook = content.index("python3 -m codex_local_harness.sandbox_mounts")
    assert hook < content.index('docker "${docker_args[@]}"')
    assert 'refusing to start worker with an invalid sandbox mount configuration' in content
    assert 'docker_args+=(--mount "$sandbox_mount")' in content
    assert content.index('mkdir -p -- "${sandbox_target%,readonly}"') < content.index('docker_args+=(--mount "$sandbox_mount")')
    assert "codex-local-worker:0.3.0" in content
