"""Exercise worker argument construction without a Docker daemon or inference."""
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("convergence", ["0", "1"])
def test_effective_reviewer_container_arguments(tmp_path, convergence):
    root = Path(__file__).resolve().parents[1]
    worker = (root / "bin/codex-local-worker-v4").read_text()
    start = worker.index("execution_env=()")
    stop = worker.index('  if [[ $convergence == 1 && -n ${CODEX_LOCAL_VERIFY_COMMAND:-} ]]', start)
    # Execute the actual production array construction, stopping before Docker.
    script = worker[start:stop] + '\nfi\nprintf "%s\\0" "${docker_args[@]}"\n'
    checkout, runtime, scratch = (tmp_path / name for name in ("checkout", "runtime", "scratch"))
    env = os.environ | {
        "worker_role": "reviewer", "worker_sandbox": "container",
        "worker_run_id": "20261101T000000Z-12345",
        "worker_runtime": "opencode", "worker_memory_test_db": "0",
        "worker_repo_path": str(checkout), "worker_runtime_home": str(runtime),
        "review_runtime_tmp": str(scratch), "worker_opencode": "/fixture/opencode",
        "convergence": convergence, "CODEX_LOCAL_SANDBOX_MOUNTS": str(tmp_path / "absent.json"),
    }
    result = subprocess.run(["bash", "-eu", "-c", script], env=env, capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
    args = result.stdout.decode().rstrip("\0").split("\0")
    values = [args[index + 1] for index, value in enumerate(args) if value == "--env"]
    environment = dict(value.split("=", 1) for value in values)
    assert environment["TMPDIR"] == str(scratch)
    assert environment["PYTHONPYCACHEPREFIX"] == str(scratch / "pycache")
    assert environment["COVERAGE_FILE"] == str(scratch / ".coverage")
    assert environment["PYTHONPATH"] == str(checkout)
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert environment["PYTEST_ADDOPTS"] == "-p no:cacheprovider"
    mounts = [args[index + 1] for index, value in enumerate(args) if value == "--mount"]
    assert f"type=bind,src={scratch},dst={scratch}" in mounts
    assert f"type=bind,src={checkout},dst={checkout},readonly" in mounts
    assert args[args.index("--tmpfs") + 1] == "/tmp:rw,nosuid,nodev,size=1g"
