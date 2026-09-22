import hashlib
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASELINE_WORKER_SHA256 = "37ff3c637f71105fd14f3a700d2e7a95d0a680851ba4d502c5f05a076eabd415"
BASELINE_FREEZE_SHA256 = "3a009b1aa169f07ed9307420c6d504d219035c7dde4ba0747de1dfede479c902"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_candidate_does_not_modify_frozen_baseline_files():
    assert digest(ROOT / "bin/codex-local-worker") == BASELINE_WORKER_SHA256
    assert digest(ROOT / "profiles/baseline-v2.freeze.json") == BASELINE_FREEZE_SHA256


def test_candidate_worker_has_valid_shell_syntax_and_new_source_boundary():
    worker = ROOT / "bin/codex-local-worker-v4"
    subprocess.run(["bash", "-n", str(worker)], check=True)
    content = worker.read_text(encoding="utf-8")
    assert "/runtime/current/src" in content
    assert "codex_harness.runtime.opencode" not in content
    assert "codex_local_harness.runtime.opencode" in content
    assert "CODEX_LOCAL_HEALTH_RECORDS" in content
    assert "CODEX_LOCAL_AGENT" in content
    assert 'worker_runtime=$(jq -r' in content
    assert "qwen-code is qualified only for native coder routes" in content
    assert "qwen --auth-type openai --approval-mode yolo" in content
    assert "qwen-code requires container confinement" in content
    assert "runtime.advisory_output" in content
    assert "select(.type == \"text\") | .part.text" in content
    assert "helper) default_worker_timeout=300" in content
    assert "reviewer) default_worker_timeout=120" in content
    assert "CODEX_LOCAL_REVIEWER_TOKEN_BUDGET" in content
    assert "CODEX_LOCAL_MAX_TASK_CHARS" in content
    assert "pass issue references and bounded evidence instead of transcripts" in content
    assert ".agent.reviewer.steps = 4" in content
    assert '.agent.reviewer.permission = {"*": "deny", "vision-review_review_screenshot": "allow"}' in content
    assert '"*": "deny",\n          "git status*"' not in content
    assert '"external_directory": "deny", "bash": "deny", "read": "deny"' not in content
    assert "CODEX_LOCAL_SESSION_BRIDGE" in content
    assert "CODEX_LOCAL_MODEL_MANAGER" in content
    assert "CODEX_LOCAL_MODEL_LOCK" in content
    assert 'CODEX_LOCAL_MODEL_LOCK_HELD=1 "$model_manager" use "$vllm_profile"' in content
    assert "unsupported vLLM worker model" in content
    assert "opencode serve --hostname 127.0.0.1" in content
    assert "codex_local_harness.opencode_bridge" in content
    assert "--env-file" in content
    assert "docker stop --time 5" in content
    assert ".state = $state | .credential_file = null" in content
    assert 'rm -f -- "$worker_control_secret"' in content
    assert "Maximum steps reached for this agent" in content
    assert "reviewer_has_result" in content
    assert 'startswith("REVIEW_RESULT_JSON=")' in content
    assert "reviewer_budget_reason && $reviewer_has_result == 0" in content
    assert "REVIEW_INCOMPLETE_JSON=" in (ROOT / "src/codex_local_harness/review.py").read_text(encoding="utf-8")
    assert "delivery-dirty-checkout" in content
    assert "PYTHONDONTWRITEBYTECODE=1" in content
    assert "diagnostic_started=0" in content
    assert "codex_local_harness.diagnostic_cli lifecycle" in content
    assert "130|143) diagnostic_event=interrupted" in content
    assert "124|137) diagnostic_event=timed-out" in content
    assert "execution-timeout" in content
    assert "worker_workflow_stage=review" in content
    assert "codex_local_harness.observe_cli workflow" in content
    assert "record_publication_failure" in content
    assert "CODEX_LOCAL_SOURCE" in content
    assert "/runtime/current/src" in content
    assert "--allow-malformed" in content
    assert "workflow_state=interrupted" in content
    assert 'workflow_identity_args+=(--pull-request "$worker_pr")' in content
    assert 'lifecycle_checkout_args=(--checkout-kind "$checkout_kind" --checkout-id "$checkout_id")' in content


def test_default_routes_split_native_qwen_coder_and_native_opencode_reviewer():
    routes = json.loads((ROOT / "config/workers-vllm.json").read_text(encoding="utf-8"))
    assert routes["routes"]["coder/complex"]["runtime"] == "qwen-code"
    assert routes["routes"]["coder/complex"]["mode"] == "native"
    assert routes["routes"]["reviewer/standard"]["runtime"] == "opencode"
    assert routes["routes"]["reviewer/standard"]["mode"] == "native"
    assert routes["routes"]["coder/tailored"]["mode"] == "tailored"
    assert routes["routes"]["reviewer/tailored"]["mode"] == "tailored"


def test_worker_image_pins_native_qwen_code_runtime():
    dockerfile = (ROOT / "docker/worker/Dockerfile").read_text(encoding="utf-8")
    assert "FROM node:22-bookworm-slim" in dockerfile
    assert "@qwen-code/qwen-code@0.22.0" in dockerfile


def test_qwen_stream_health_parser_uses_result_usage(tmp_path):
    from codex_local_harness.runtime.opencode import parse_log

    log = tmp_path / "qwen.jsonl"
    log.write_text(
        '{"type":"assistant","message":{"content":[{"type":"tool_use"}]}}\n'
        '{"type":"result","duration_ms":1250,"num_turns":2,'
        '"usage":{"input_tokens":10,"output_tokens":4,"cache_read_input_tokens":3}}\n',
        encoding="utf-8",
    )
    stats = parse_log(log, "qwen-code")
    assert (stats.input_tokens, stats.output_tokens, stats.turns, stats.tool_calls) == (10, 4, 2, 1)
    assert stats.wall_seconds == 1.25


def test_candidate_advisory_agent_is_read_only_and_machine_readable():
    content = (ROOT / "agents/advisory.md").read_text(encoding="utf-8")
    assert "edit: deny" in content
    assert "bash: deny" in content
    assert "task: deny" in content
    assert "steps: 12" in content
    assert "final response must be only" in content
    assert "no analysis" in content


def test_candidate_profile_limits_qwen38_to_qualified_advisory_scope():
    profile = json.loads((ROOT / "profiles/codex-led-v4.json").read_text(encoding="utf-8"))
    assert profile["routes"]["helper"]["model"] == "qwen/qwen3.8-27b"
    assert profile["routes"]["helper"]["qualification"].startswith("not-promoted")
    assert profile["routes"]["scout"]["authority"] == "advisory-only"
    assert profile["routes"]["scout"]["runtime"] == "direct-lm-studio"
    assert "qualified" in profile["routes"]["scout"]["qualification"]
    assert profile["routes"]["test_designer"]["qualification"].startswith("not-promoted")
    assert profile["routes"]["failure_analyst"]["qualification"].startswith("not-promoted")
    assert profile["routes"]["complex_coder"]["model"] == "Qwen/Qwen3-Coder-Next-FP8"
    assert profile["governance"]["merge_authority"] == "codex"
