from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path


PROJECT = Path("/home/connellwc83/projects/codex/local-harness")
RELEASE_MANIFEST = Path(__file__).resolve().parents[1] / "config" / "installed-release.json"
QUALIFIED_ROUTES = Path(__file__).resolve().parents[1] / "config" / "qualified-qwen38-routes.json"
PROJECT_WORKER = PROJECT / "bin/codex-local-worker"
INSTALLED_WORKER = Path("/home/connellwc83/.local/bin/codex-local-worker")
PROJECT_CRITIC = PROJECT / "bin/codex-local-critic"
INSTALLED_CRITIC = Path("/home/connellwc83/.local/bin/codex-local-critic")
OPENCODE_CONFIG = Path("/home/connellwc83/.config/opencode/opencode.json")
REVIEWER_PROMPT = Path("/home/connellwc83/.config/opencode/agents/reviewer.md")
CODER_PROMPT = Path("/home/connellwc83/.config/opencode/agents/coder.md")
ORCHESTRATOR_PROMPT = Path("/home/connellwc83/.config/opencode/agents/orchestrator.md")


def run(argv, **kwargs):
    return subprocess.run(argv, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)


def test_project_worker_has_valid_shell_syntax():
    completed = run(["bash", "-n", str(PROJECT_WORKER)])
    assert completed.returncode == 0, completed.stderr


def test_reviewer_runtime_disables_checkout_cache_writes():
    worker = PROJECT_WORKER.read_text(encoding="utf-8")
    assert "PYTHONDONTWRITEBYTECODE=1" in worker
    assert 'PYTEST_ADDOPTS="-p no:cacheprovider"' in worker
    assert 'PYTHONPYCACHEPREFIX="$review_runtime_tmp/pycache"' in worker
    assert 'COVERAGE_FILE="$review_runtime_tmp/.coverage"' in worker
    assert 'PYTHONPATH="$worker_repo_path"' in worker
    assert '--scratch-dir "$review_runtime_tmp"' in worker
    assert '--runtime-home "$worker_runtime_home"' in worker
    assert 'reviewer-scratch' in worker


def test_installed_worker_matches_a_qualified_project_copy():
    release = json.loads(RELEASE_MANIFEST.read_text(encoding="utf-8"))
    installed_digest = hashlib.sha256(INSTALLED_WORKER.read_bytes()).hexdigest()
    assert installed_digest in release["qualified_worker_sha256"]


def test_candidate_worker_does_not_require_self_promotion():
    candidate = Path(__file__).resolve().parents[1] / "bin" / "codex-local-worker-v4"
    assert run(["bash", "-n", str(candidate)]).returncode == 0


def test_critic_uses_same_sterile_inference_boundary():
    critic = PROJECT_CRITIC.read_text(encoding="utf-8")
    assert 'env -i HOME="$critic_runtime_home"' in critic
    assert 'XDG_CONFIG_HOME="$critic_runtime_home/.config"' in critic
    assert '.mcp.github.enabled = false' in critic
    assert "HF_TOKEN" not in critic


def test_installed_critic_matches_qualified_project_copy():
    assert INSTALLED_CRITIC.read_bytes() == PROJECT_CRITIC.read_bytes()


def test_reviewer_fails_before_inference_without_transaction_identity(tmp_path):
    env = os.environ.copy()
    for name in ("CODEX_LOCAL_REPOSITORY", "CODEX_LOCAL_ISSUE", "CODEX_LOCAL_PR"):
        env.pop(name, None)
    completed = run([str(PROJECT_WORKER), "reviewer", str(tmp_path), "review this"], env=env)
    assert completed.returncode == 2
    assert "reviewer requires CODEX_LOCAL_REPOSITORY" in completed.stderr
    assert "Starting local worker" not in completed.stderr


def test_candidate_coder_postflight_is_opt_in_and_deterministic():
    worker = PROJECT_WORKER.read_text(encoding="utf-8")
    assert "CODEX_LOCAL_CODER_POSTFLIGHT" in worker
    assert "codex_local_harness.coder" in worker
    assert "HARNESS DELIVERY BOUNDARY" in worker
    assert '"$worker_task"' in worker


def test_worker_inference_uses_sterile_allowlisted_environment():
    worker = PROJECT_WORKER.read_text(encoding="utf-8")
    assert "worker_runtime_home=$(mktemp -d" in worker
    assert 'worker_env=(env -i HOME="$worker_runtime_home"' in worker
    assert 'XDG_CONFIG_HOME="$worker_runtime_home/.config"' in worker
    assert '.mcp.github.enabled = false' in worker
    assert '"git push *": "deny"' in worker
    assert '"curl *": "deny"' in worker
    assert "HF_TOKEN" not in worker


def test_worker_does_not_pass_ambient_secret_to_inference(tmp_path):
    fake = tmp_path / "fake-opencode"
    fake.write_text(
        "#!/bin/sh\n"
        "printf '{\"type\":\"text\",\"part\":{\"text\":\"SECRET=%s HOME=%s\"}}\\n' "
        '"${HARNESS_SECRET_SENTINEL-unset}" "$HOME"\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    routes = tmp_path / "workers.json"
    routes.write_text(
        json.dumps({
            "defaults": {"helper": "standard"},
            "routes": {"helper/standard": {"agent": "helper", "model": "vllm/test"}},
        }),
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    env = os.environ.copy()
    env.update({
        "CODEX_LOCAL_WORKER_CONFIG": str(routes),
        "CODEX_LOCAL_OPENCODE": str(fake),
        "CODEX_LOCAL_SESSION_BRIDGE": "0",
        "HARNESS_SECRET_SENTINEL": "must-not-cross-boundary",
    })
    completed = run([str(PROJECT_WORKER), "helper", str(repo), "inspect"], env=env)
    assert completed.returncode == 0, completed.stderr
    assert "SECRET=unset" in completed.stdout
    assert "must-not-cross-boundary" not in completed.stdout
    runtime_home = Path(completed.stdout.split("HOME=", 1)[1].strip())
    assert runtime_home != Path.home()
    assert not runtime_home.exists()


def test_coder_prompt_assigns_publication_to_deterministic_harness():
    prompt = CODER_PROMPT.read_text(encoding="utf-8")
    assert "Do not push, create a pull request, comment on an issue, or change labels" in prompt
    assert "deterministic harness publisher" in prompt


def test_dry_run_reports_exact_route_without_inference(tmp_path):
    env = os.environ.copy()
    env["CODEX_LOCAL_DRY_RUN"] = "1"
    env["CODEX_LOCAL_WORKER_CONFIG"] = str(QUALIFIED_ROUTES)
    completed = run([str(PROJECT_WORKER), "reviewer", str(tmp_path), "review this"], env=env)
    assert completed.returncode == 0
    assert "role=reviewer" in completed.stdout
    assert "model=vllm/Qwen/Qwen3.8-27B-FP8" in completed.stdout
    assert "Starting local worker" not in completed.stderr


def test_reviewer_configuration_denies_direct_durable_writes():
    config = json.loads(OPENCODE_CONFIG.read_text(encoding="utf-8"))
    permission = config["agent"]["reviewer"]["permission"]
    assert permission["edit"] == "deny"
    assert permission["task"] == "deny"
    assert permission["github_*"] == "deny"
    assert permission["gitea_*"] == "deny"
    assert permission["bash"]["gh *"] == "deny"
    assert permission["bash"]["git push *"] == "deny"
    assert permission["bash"]["pip *"] == "deny"
    assert permission["bash"]["python3 -m pip *"] == "deny"
    assert permission["bash"]["*&& rm *"] == "deny"
    assert permission["bash"]["* gh *"] == "deny"
    assert permission["bash"]["*/tmp/*"] == "deny"
    assert permission["bash"]["* /tmp/*"] == "deny"


def test_coder_configuration_denies_subagent_delegation():
    config = json.loads(OPENCODE_CONFIG.read_text(encoding="utf-8"))
    assert config["agent"]["coder"]["permission"]["task"] == "deny"


def test_reviewer_prompt_assigns_publication_to_deterministic_harness():
    prompt = REVIEWER_PROMPT.read_text(encoding="utf-8")
    assert "no durable-state ownership" in prompt
    assert "REVIEW_RESULT_JSON=" in prompt
    assert "Do not claim that you posted comments, reviews, or labels" in prompt
    assert "Do not install the project or any package" in prompt


def test_state_transition_ownership_is_not_contradictory():
    prompt = ORCHESTRATOR_PROMPT.read_text(encoding="utf-8")
    assert "owned by the deterministic publisher" in prompt
    assert "owned by Reviewer" not in prompt
    assert "unsupported verdicts as invalid output" in prompt
