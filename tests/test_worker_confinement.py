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


def test_qwen_runtime_rejects_non_coder_route(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    routes = tmp_path / "routes.json"
    routes.write_text(json.dumps({
        "defaults": {"helper": "standard"},
        "routes": {"helper/standard": {
            "agent": "helper", "model": "vllm/test",
            "runtime": "qwen-code", "mode": "native",
        }},
    }), encoding="utf-8")
    completed = subprocess.run(
        [str(WORKER), "helper", str(repo), "inspect"],
        env=os.environ | {"CODEX_LOCAL_WORKER_CONFIG": str(routes), "CODEX_LOCAL_DRY_RUN": "1"},
        text=True, capture_output=True,
    )
    assert completed.returncode == 2
    assert "qualified only for native coder routes" in completed.stderr


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


def test_disposable_opencode_config_contains_qwen35_nvfp4_model():
    content = WORKER.read_text(encoding="utf-8")
    assert '.provider.vllm.models."nvidia/Qwen3.5-122B-A10B-NVFP4" = {' in content
    assert '"context": 65536' in content
    assert '"output": 8192' in content
    assert '"name": "Qwen3.5 122B NVFP4 (Spark)"' in content


def test_vllm_worker_allowlist_includes_qwen35_nvfp4():
    content = WORKER.read_text(encoding="utf-8")
    assert 'nvidia/Qwen3.5-122B-A10B-NVFP4) vllm_profile=qwen35-nvfp4' in content


def test_worker_config_supports_deepseek_v4_reasoning():
    content = WORKER.read_text(encoding="utf-8")
    assert 'deepseek/deepseek-v4-flash-0731-exl3-k2) vllm_profile=deepseek-v4-reasoning' in content
    assert '.provider.deepseek = {' in content
    assert '"context": 1000000' in content
    assert '"output": 32768' in content
    assert '"baseURL": "http://127.0.0.1:8011/v1"' in content


def test_native_qwen_code_caps_qwen35_output_tokens():
    content = WORKER.read_text(encoding="utf-8")
    assert "if [[ $qwen_model == nvidia/Qwen3.5-122B-A10B-NVFP4 ]]" in content
    assert "qwen_env+=(--env QWEN_CODE_MAX_OUTPUT_TOKENS=8192)" in content
    assert '"${qwen_env[@]}"' in content


def test_deepseek_routes_resolve_through_real_worker_and_investigator(tmp_path):
    env = os.environ | {
        "CODEX_LOCAL_WORKER_CONFIG": str(ROOT / "config/workers-vllm.json"),
        "CODEX_LOCAL_DRY_RUN": "1",
        "CODEX_LOCAL_WORKER": str(WORKER),
    }
    env.pop("CODEX_LOCAL_MODEL", None)
    env.pop("CODEX_LOCAL_TIER", None)
    for role, tier, expected in (
        ("coder", None, "vllm/Qwen/Qwen3-Coder-Next-FP8"),
        ("reviewer", None, "deepseek/deepseek-v4-flash-0731-exl3-k2"),
        ("coder", "deepseek", "deepseek/deepseek-v4-flash-0731-exl3-k2"),
    ):
        case_env = env | ({"CODEX_LOCAL_TIER": tier} if tier else {})
        result = subprocess.run([str(WORKER), role, str(tmp_path), "inspect"],
                                env=case_env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert f"model={expected}" in result.stdout
    result = subprocess.run([str(ROOT / "bin/codex-local-investigator"), str(tmp_path), "inspect"],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "role=helper" in result.stdout
    assert "tier=investigator" in result.stdout
    assert "model=deepseek/deepseek-v4-flash-0731-exl3-k2" in result.stdout


def test_investigator_receives_contract_issue_and_correct_endpoint(tmp_path):
    fake = tmp_path / "fake-opencode"
    fake.write_text(
        '#!/usr/bin/env python3\nimport json, os, pathlib, sys\n'
        'config=json.loads((pathlib.Path(os.environ["XDG_CONFIG_HOME"])/"opencode/opencode.json").read_text())\n'
        'assert config["provider"]["deepseek"]["options"]["baseURL"] == "http://127.0.0.1:8011/v1"\n'
        'packet=sys.argv[-1]\n'
        'assert "Separate observations, hypotheses, and conclusions" in packet\n'
        'assert "## Objective\\nInspect the serializer" in packet\n'
        'assert (pathlib.Path(os.environ["HOME"])/"structured-task.txt").read_text().strip() == packet.strip()\n'
        'print(json.dumps({"type":"text","part":{"text":"PACKET_OK"}}))\n'
    )
    fake.chmod(0o755)
    env = os.environ | {
        "CODEX_LOCAL_WORKER_CONFIG": str(ROOT / "config/workers-vllm.json"),
        "CODEX_LOCAL_WORKER": str(WORKER), "CODEX_LOCAL_OPENCODE": str(fake),
        "CODEX_LOCAL_MODEL_SWITCH": "0", "CODEX_LOCAL_SESSION_BRIDGE": "0",
        "CODEX_LOCAL_SANDBOX": "disabled", "CODEX_LOCAL_ALLOW_UNSANDBOXED": "1",
        "CODEX_LOCAL_RECORD_RUN": "0",
    }
    env.pop("CODEX_LOCAL_MODEL", None)
    env.pop("CODEX_LOCAL_DRY_RUN", None)
    result = subprocess.run([str(ROOT / "bin/codex-local-investigator"), str(tmp_path),
                             "## Objective\nInspect the serializer"],
                            env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "PACKET_OK" in result.stdout
