import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "bin/codex-local-model"
ROUTES = ROOT / "config/workers-vllm.json"


def _fake_runtime(tmp_path: Path, *, updates_model: bool = True):
    state = tmp_path / "model"
    calls = tmp_path / "docker.calls"
    curl = tmp_path / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        'case "$*" in *8011*) FAKE_MODEL_STATE="$FAKE_DEEPSEEK_STATE" ;; esac\n'
        'if [ -s "$FAKE_MODEL_STATE" ]; then '
        'jq -n --arg id "$(cat "$FAKE_MODEL_STATE")" \'{data:[{id:$id}]}\'; '
        "else echo '{\"data\":[]}'; fi\n",
        encoding="utf-8",
    )
    docker = tmp_path / "docker"
    update = (
        'case "$*" in *Qwen3.8-27B-FP8*) printf %s Qwen/Qwen3.8-27B-FP8 >"$FAKE_MODEL_STATE" ;; '
        '*Qwen3-Coder-Next-FP8*) printf %s Qwen/Qwen3-Coder-Next-FP8 >"$FAKE_MODEL_STATE" ;; '
        '*Qwen3.5-122B-A10B-NVFP4*) printf %s nvidia/Qwen3.5-122B-A10B-NVFP4 >"$FAKE_MODEL_STATE" ;; esac\n'
        if updates_model else ""
    )
    docker.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >>"$FAKE_DOCKER_CALLS"\n'
        'if [ "$*" = "inspect vllm_node" ] && [ "${FAKE_CONTAINER_MISSING:-0}" = 1 ]; then exit 1; fi\n'
        'case "$*" in "inspect --format"*) printf "%s\\n" "${FAKE_DEEPSEEK_RUNNING:-false}" ;; esac\n'
        'case "$*" in *pkill*|"stop vllm_node") : >"$FAKE_MODEL_STATE" ;; esac\n' + update,
        encoding="utf-8",
    )
    sleeper = tmp_path / "sleep"
    deepseek = tmp_path / "deepseek"
    recipe = tmp_path / "recipe"
    recipe.write_text(
        '#!/bin/sh\nprintf "recipe %s\\n" "$*" >>"$FAKE_DOCKER_CALLS"\n'
        'printf %s Qwen/Qwen3-Coder-Next-FP8 >"$FAKE_MODEL_STATE"\n'
    )
    deepseek.write_text(
        '#!/bin/sh\nprintf "deepseek %s\\n" "$*" >>"$FAKE_DOCKER_CALLS"\n'
        'case "$1" in stop) : >"$FAKE_DEEPSEEK_STATE" ;; '
        'start) ' + ('printf %s deepseek-v4-flash-0731-exl3-k2 >"$FAKE_DEEPSEEK_STATE"' if updates_model else ':') + ' ;; esac\n'
    )
    sleeper.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    for executable in (curl, docker, sleeper, deepseek, recipe):
        executable.chmod(0o755)
    env = os.environ | {
        "CODEX_LOCAL_CURL": str(curl),
        "CODEX_LOCAL_DOCKER": str(docker),
        "CODEX_LOCAL_SLEEP": str(sleeper),
        "CODEX_LOCAL_MODEL_LOCK": str(tmp_path / "model.lock"),
        "CODEX_LOCAL_MODEL_READY_ATTEMPTS": "2",
        "CODEX_LOCAL_MODEL_STOP_ATTEMPTS": "2",
        "FAKE_MODEL_STATE": str(state),
        "FAKE_DEEPSEEK_STATE": str(tmp_path / "deepseek-model"),
        "CODEX_LOCAL_DEEPSEEK_LAUNCHER": str(deepseek),
        "CODEX_LOCAL_RECIPE_LAUNCHER": str(recipe),
        "FAKE_DOCKER_CALLS": str(calls),
    }
    return state, calls, env


def test_vllm_routes_assign_both_models_without_lm_studio():
    config = json.loads(ROUTES.read_text(encoding="utf-8"))
    assert config["routes"]["coder/complex"]["model"] == "vllm/Qwen/Qwen3-Coder-Next-FP8"
    assert config["routes"]["coder/standard"]["model"] == "vllm/Qwen/Qwen3-Coder-Next-FP8"
    assert config["routes"]["reviewer/standard"]["model"] == "deepseek/deepseek-v4-flash-0731-exl3-k2"
    assert config["routes"]["coder/experimental-qwen38"]["model"] == "vllm/Qwen/Qwen3.8-27B-FP8"
    assert config["routes"]["coder/experimental-qwen35-nvfp4"]["model"] == "vllm/nvidia/Qwen3.5-122B-A10B-NVFP4"
    assert config["routes"]["helper/experimental-qwen35-nvfp4"]["model"] == "vllm/nvidia/Qwen3.5-122B-A10B-NVFP4"
    assert config["routes"]["reviewer/experimental-qwen35-nvfp4"]["model"] == "vllm/nvidia/Qwen3.5-122B-A10B-NVFP4"
    assert config["routes"]["helper/investigator"]["model"] == "deepseek/deepseek-v4-flash-0731-exl3-k2"
    assert config["routes"]["coder/deepseek"]["model"] == "deepseek/deepseek-v4-flash-0731-exl3-k2"
    assert all(not route["model"].startswith("lmstudio/") for route in config["routes"].values())


def test_model_manager_is_idempotent_then_switches_exact_model(tmp_path):
    state, calls, env = _fake_runtime(tmp_path)
    state.write_text("Qwen/Qwen3.8-27B-FP8", encoding="utf-8")
    first = subprocess.run([str(MANAGER), "use", "qwen38"], env=env, text=True, capture_output=True)
    assert first.returncode == 0
    assert "already active" in first.stdout
    assert not calls.exists()

    switched = subprocess.run([str(MANAGER), "use", "coder-next"], env=env, text=True, capture_output=True)
    assert switched.returncode == 0, switched.stderr
    assert state.read_text(encoding="utf-8") == "Qwen/Qwen3-Coder-Next-FP8"
    assert "pkill" in calls.read_text(encoding="utf-8")
    docker_calls = calls.read_text(encoding="utf-8")
    assert "--enable-auto-tool-choice" in docker_calls
    assert "--tool-call-parser qwen3_coder" in docker_calls
    assert "--max-model-len 185000" in docker_calls
    assert "--gpu-memory-utilization 0.87" in docker_calls
    assert "--load-format fastsafetensors" in docker_calls
    assert "--attention-backend flashinfer" in docker_calls
    assert "--enable-prefix-caching" in docker_calls
    assert "--host 0.0.0.0" in docker_calls
    assert "--port 8000" in docker_calls


def test_model_manager_switches_to_qwen35_nvfp4_profile(tmp_path):
    state, calls, env = _fake_runtime(tmp_path)
    state.write_text("Qwen/Qwen3-Coder-Next-FP8", encoding="utf-8")
    first = subprocess.run([str(MANAGER), "use", "qwen35-nvfp4"], env=env, text=True, capture_output=True)
    assert first.returncode == 0, first.stderr
    assert state.read_text(encoding="utf-8") == "nvidia/Qwen3.5-122B-A10B-NVFP4"
    assert "pkill" in calls.read_text(encoding="utf-8")
    docker_calls = calls.read_text(encoding="utf-8")
    assert "--trust-remote-code" in docker_calls
    assert "--quantization modelopt_fp4" in docker_calls
    assert "--kv-cache-dtype fp8" in docker_calls
    assert "--tensor-parallel-size 1" in docker_calls
    assert "--max-model-len 65536" in docker_calls
    assert "--gpu-memory-utilization 0.82" in docker_calls
    assert "--enforce-eager" in docker_calls
    assert "--max-num-seqs 8" in docker_calls
    assert "--max-num-batched-tokens 8192" in docker_calls
    assert "--reasoning-parser qwen3" in docker_calls
    assert "--enable-auto-tool-choice" in docker_calls
    assert "--tool-call-parser qwen3_coder" in docker_calls
    assert "--language-model-only" in docker_calls
    assert "--attention-backend flashinfer" in docker_calls
    assert "--enable-prefix-caching" in docker_calls
    assert "--load-format fastsafetensors" in docker_calls
    assert "--host 0.0.0.0" in docker_calls
    assert "--port 8000" in docker_calls


def test_model_manager_fails_when_exact_model_never_becomes_ready(tmp_path):
    state, _, env = _fake_runtime(tmp_path, updates_model=False)
    state.write_text("Qwen/Qwen3.8-27B-FP8", encoding="utf-8")
    completed = subprocess.run([str(MANAGER), "use", "coder-next"], env=env, text=True, capture_output=True)
    assert completed.returncode == 69
    assert "exact model Qwen/Qwen3-Coder-Next-FP8" in completed.stderr


def test_model_manager_rejects_unqualified_profile():
    completed = subprocess.run([str(MANAGER), "use", "arbitrary"], text=True, capture_output=True)
    assert completed.returncode == 2
    assert "usage:" in completed.stderr


def test_model_manager_launches_qwen38_profile(tmp_path):
    state, calls, env = _fake_runtime(tmp_path)
    state.write_text("Qwen/Qwen3-Coder-Next-FP8", encoding="utf-8")
    first = subprocess.run([str(MANAGER), "use", "qwen38"], env=env, text=True, capture_output=True)
    assert first.returncode == 0, first.stderr
    assert state.read_text(encoding="utf-8") == "Qwen/Qwen3.8-27B-FP8"
    assert "pkill" in calls.read_text(encoding="utf-8")
    docker_calls = calls.read_text(encoding="utf-8")
    assert "--reasoning-parser qwen3" in docker_calls
    assert "--enable-auto-tool-choice" in docker_calls
    assert "--tool-call-parser qwen3_coder" in docker_calls
    assert "--max-model-len 185000" in docker_calls
    assert "--gpu-memory-utilization 0.87" in docker_calls
    assert "--load-format fastsafetensors" in docker_calls
    assert "--attention-backend flashinfer" in docker_calls
    assert "--enable-prefix-caching" in docker_calls
    assert "--host 0.0.0.0" in docker_calls
    assert "--port 8000" in docker_calls


def test_model_manager_qwen35_nvfp4_safe_boundaries(tmp_path):
    state, calls, env = _fake_runtime(tmp_path)
    state.write_text("Qwen/Qwen3-Coder-Next-FP8", encoding="utf-8")
    first = subprocess.run([str(MANAGER), "use", "qwen35-nvfp4"], env=env, text=True, capture_output=True)
    assert first.returncode == 0, first.stderr
    docker_calls = calls.read_text(encoding="utf-8")
    assert "--max-model-len 65536" in docker_calls
    assert "--gpu-memory-utilization 0.82" in docker_calls
    assert "--enforce-eager" in docker_calls
    assert "--max-num-seqs 8" in docker_calls
    assert "--max-num-batched-tokens 8192" in docker_calls
    assert "--reasoning-parser qwen3" in docker_calls
    assert "--enable-auto-tool-choice" in docker_calls
    assert "--tool-call-parser qwen3_coder" in docker_calls
    assert "--language-model-only" in docker_calls


def test_model_manager_launches_deepseek_v4_reasoning_profile(tmp_path):
    state, calls, env = _fake_runtime(tmp_path)
    state.write_text("Qwen/Qwen3-Coder-Next-FP8", encoding="utf-8")
    first = subprocess.run([str(MANAGER), "use", "deepseek-v4-reasoning"], env=env, text=True, capture_output=True)
    assert first.returncode == 0, first.stderr
    assert not state.read_text()
    assert Path(env["FAKE_DEEPSEEK_STATE"]).read_text() == "deepseek-v4-flash-0731-exl3-k2"
    docker_calls = calls.read_text(encoding="utf-8")
    assert docker_calls.index("stop vllm_node") < docker_calls.index("deepseek start")
    again = subprocess.run([str(MANAGER), "use", "deepseek-v4-reasoning"], env=env, capture_output=True)
    assert again.returncode == 0
    assert calls.read_text() == docker_calls
    back = subprocess.run([str(MANAGER), "use", "coder-next"], env=env, capture_output=True)
    assert back.returncode == 0
    assert state.read_text() == "Qwen/Qwen3-Coder-Next-FP8"
    assert not Path(env["FAKE_DEEPSEEK_STATE"]).read_text()
    assert calls.read_text().index("deepseek stop") < calls.read_text().index("start vllm_node")


def test_model_manager_deepseek_v4_reasoning_safe_boundaries(tmp_path):
    state, calls, env = _fake_runtime(tmp_path)
    state.write_text("Qwen/Qwen3-Coder-Next-FP8", encoding="utf-8")
    Path(env["FAKE_DEEPSEEK_STATE"]).write_text("deepseek-v4-flash-0731-exl3-k2")
    first = subprocess.run([str(MANAGER), "use", "deepseek-v4-reasoning"], env=env, text=True, capture_output=True)
    assert first.returncode == 69
    assert "both model endpoints" in first.stderr
    assert not calls.exists()


def test_model_manager_fails_when_deepseek_model_never_becomes_ready(tmp_path):
    state, _, env = _fake_runtime(tmp_path, updates_model=False)
    state.write_text("Qwen/Qwen3-Coder-Next-FP8", encoding="utf-8")
    completed = subprocess.run([str(MANAGER), "use", "deepseek-v4-reasoning"], env=env, text=True, capture_output=True)
    assert completed.returncode == 69
    assert "exact model deepseek-v4-flash-0731-exl3-k2" in completed.stderr


def test_switch_stops_deepseek_with_dead_api_before_starting_coder(tmp_path):
    state, calls, env = _fake_runtime(tmp_path)
    env["FAKE_DEEPSEEK_RUNNING"] = "true"
    result = subprocess.run([str(MANAGER), "use", "coder-next"], env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert state.read_text() == "Qwen/Qwen3-Coder-Next-FP8"
    commands = calls.read_text()
    assert commands.index("deepseek stop") < commands.index("start vllm_node")


def test_return_to_coder_recreates_auto_removed_container_via_recipe(tmp_path):
    state, calls, env = _fake_runtime(tmp_path)
    env["FAKE_CONTAINER_MISSING"] = "1"
    Path(env["FAKE_DEEPSEEK_STATE"]).write_text("deepseek-v4-flash-0731-exl3-k2")
    result = subprocess.run([str(MANAGER), "use", "coder-next"], env=env,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert state.read_text() == "Qwen/Qwen3-Coder-Next-FP8"
    commands = calls.read_text()
    assert commands.index("deepseek stop") < commands.index("recipe qwen3-coder-next-fp8")
    assert "--gpu-mem 0.82 --max-model-len 185000 --daemon" in commands


def test_model_manager_uses_default_gpu_mem_0_82_when_unspecified(tmp_path):
    state, calls, env = _fake_runtime(tmp_path)
    state.write_text("Qwen/Qwen3-Coder-Next-FP8", encoding="utf-8")
    # Do not set CODEX_LOCAL_VLLM_GPU_MEM to test default
    first = subprocess.run([str(MANAGER), "use", "qwen38"], env=env, text=True, capture_output=True)
    assert first.returncode == 0, first.stderr
    assert state.read_text(encoding="utf-8") == "Qwen/Qwen3.8-27B-FP8"
    docker_calls = calls.read_text(encoding="utf-8")
    assert "--gpu-memory-utilization 0.82" in docker_calls


def test_model_manager_allows_override_via_CODEX_LOCAL_VLLM_GPU_MEM(tmp_path):
    state, calls, env = _fake_runtime(tmp_path)
    state.write_text("Qwen/Qwen3-Coder-Next-FP8", encoding="utf-8")
    env["CODEX_LOCAL_VLLM_GPU_MEM"] = "0.85"
    first = subprocess.run([str(MANAGER), "use", "qwen38"], env=env, text=True, capture_output=True)
    assert first.returncode == 0, first.stderr
    assert state.read_text(encoding="utf-8") == "Qwen/Qwen3.8-27B-FP8"
    docker_calls = calls.read_text(encoding="utf-8")
    assert "--gpu-memory-utilization 0.85" in docker_calls


def test_model_manager_rejects_empty_CODEX_LOCAL_VLLM_GPU_MEM(tmp_path):
    state, calls, env = _fake_runtime(tmp_path)
    state.write_text("Qwen/Qwen3-Coder-Next-FP8", encoding="utf-8")
    env["CODEX_LOCAL_VLLM_GPU_MEM"] = ""
    first = subprocess.run([str(MANAGER), "use", "qwen38"], env=env, text=True, capture_output=True)
    assert first.returncode == 1
    assert "CODEX_LOCAL_VLLM_GPU_MEM is empty" in first.stderr
    assert not calls.exists()


def test_model_manager_rejects_nonfinite_CODEX_LOCAL_VLLM_GPU_MEM(tmp_path):
    state, calls, env = _fake_runtime(tmp_path)
    state.write_text("Qwen/Qwen3-Coder-Next-FP8", encoding="utf-8")
    env["CODEX_LOCAL_VLLM_GPU_MEM"] = "nan"
    first = subprocess.run([str(MANAGER), "use", "qwen38"], env=env, text=True, capture_output=True)
    assert first.returncode == 1
    assert "not a valid number" in first.stderr
    assert not calls.exists()


def test_model_manager_rejects_out_of_range_low_CODEX_LOCAL_VLLM_GPU_MEM(tmp_path):
    state, calls, env = _fake_runtime(tmp_path)
    state.write_text("Qwen/Qwen3-Coder-Next-FP8", encoding="utf-8")
    env["CODEX_LOCAL_VLLM_GPU_MEM"] = "0.5"
    first = subprocess.run([str(MANAGER), "use", "qwen38"], env=env, text=True, capture_output=True)
    assert first.returncode == 1
    assert "must be finite and in (0.5, 0.95]" in first.stderr
    assert not calls.exists()


def test_model_manager_rejects_out_of_range_high_CODEX_LOCAL_VLLM_GPU_MEM(tmp_path):
    state, calls, env = _fake_runtime(tmp_path)
    state.write_text("Qwen/Qwen3-Coder-Next-FP8", encoding="utf-8")
    env["CODEX_LOCAL_VLLM_GPU_MEM"] = "0.95"
    first = subprocess.run([str(MANAGER), "use", "qwen38"], env=env, text=True, capture_output=True)
    assert first.returncode == 1
    assert "must be finite and in (0.5, 0.95]" in first.stderr
    assert not calls.exists()


def test_model_manager_accepts_boundary_value_0_95_exclusive_upper(tmp_path):
    state, calls, env = _fake_runtime(tmp_path)
    state.write_text("Qwen/Qwen3-Coder-Next-FP8", encoding="utf-8")
    env["CODEX_LOCAL_VLLM_GPU_MEM"] = "0.9499"
    first = subprocess.run([str(MANAGER), "use", "qwen38"], env=env, text=True, capture_output=True)
    assert first.returncode == 0, first.stderr
    docker_calls = calls.read_text(encoding="utf-8")
    assert "--gpu-memory-utilization 0.9499" in docker_calls


def test_model_manager_qwen35_nvfp4_ignores_env_and_keeps_0_82(tmp_path):
    state, calls, env = _fake_runtime(tmp_path)
    state.write_text("Qwen/Qwen3-Coder-Next-FP8", encoding="utf-8")
    env["CODEX_LOCAL_VLLM_GPU_MEM"] = "0.87"
    first = subprocess.run([str(MANAGER), "use", "qwen35-nvfp4"], env=env, text=True, capture_output=True)
    assert first.returncode == 0, first.stderr
    docker_calls = calls.read_text(encoding="utf-8")
    assert "--gpu-memory-utilization 0.82" in docker_calls
