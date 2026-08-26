from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1] / "qualification" / "paired-model"
SPEC = importlib.util.spec_from_file_location("paired_benchmark", ROOT / "run.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)
RUNTIME_SPEC = importlib.util.spec_from_file_location("runtime_benchmark", ROOT / "run_runtime.py")
RUNTIME_MODULE = importlib.util.module_from_spec(RUNTIME_SPEC)
assert RUNTIME_SPEC.loader
RUNTIME_SPEC.loader.exec_module(RUNTIME_MODULE)
REVIEW_SPEC = importlib.util.spec_from_file_location("native_review", ROOT / "run_native_review.py")
REVIEW_MODULE = importlib.util.module_from_spec(REVIEW_SPEC)
assert REVIEW_SPEC.loader
REVIEW_SPEC.loader.exec_module(REVIEW_MODULE)


def test_manifest_has_ten_real_cases_and_identity_covers_task_check_policy_source():
    manifest = MODULE.load_manifest(ROOT / "manifest.json")
    assert len(manifest["cases"]) == 10
    identities = [MODULE.identity(ROOT / "manifest.json", manifest, case) for case in manifest["cases"]]
    assert len({item["identity_hash"] for item in identities}) == 10
    assert all(len(item[key]) == 64 for item in identities for key in ("source_hash", "task_hash", "check_hash", "policy_hash"))


def test_manifest_rejects_task_traversal(tmp_path: Path):
    payload = json.loads((ROOT / "manifest.json").read_text())
    payload["cases"][0]["task"] = "../outside.md"
    (tmp_path / "input").mkdir()
    (tmp_path / "manifest.json").write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="invalid task path"):
        MODULE.load_manifest(tmp_path / "manifest.json")


def test_manifest_rejects_case_id_path_traversal(tmp_path: Path):
    payload = json.loads((ROOT / "manifest.json").read_text())
    payload["cases"][0]["id"] = "../../escape"
    (tmp_path / "input").mkdir()
    for case in payload["cases"]:
        task = tmp_path / case["task"]
        task.parent.mkdir(parents=True, exist_ok=True)
        task.write_text("task")
    (tmp_path / "manifest.json").write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="malformed"):
        MODULE.load_manifest(tmp_path / "manifest.json")


def test_event_normalization_deduplicates_tool_lifecycle_and_preserves_unknown_tokens():
    events = [
        {"type":"tool_use","part":{"callID":"one","tool":"bash","state":{"status":"running"}}},
        {"type":"tool_use","part":{"callID":"one","tool":"bash","state":{"status":"completed"}}},
        {"type":"permission.asked","properties":{"id":"p1"}},
        {"type":"permission.asked","properties":{"id":"p1"}},
        {"type":"permission.replied","properties":{"id":"p1"}},
    ]
    result = MODULE.normalize_events(events)
    assert result["tool_calls"] == 1
    assert result["completed_tool_calls"] == 1
    assert result["permissions_requested"] == 1
    assert result["interventions"] == 1
    assert result["tokens_total"] is None


def test_qwen_event_normalization_records_tools_tokens_and_denials():
    events = [
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "one", "name": "write_file", "input": {}},
        ], "usage": {"total_tokens": 120}}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "one", "is_error": False},
        ]}},
        {"type": "result", "usage": {"total_tokens": 140}, "permission_denials": ["shell"]},
    ]
    result = RUNTIME_MODULE.normalize_qwen(events)
    assert result["tool_calls"] == 1
    assert result["completed_tool_calls"] == 1
    assert result["tokens_total"] == 140
    assert result["permissions_requested"] == 1
    assert result["tool_errors"] == 0


def test_runtime_environments_are_isolated_and_disable_external_capabilities(tmp_path: Path):
    opencode_config = tmp_path / "opencode.json"
    opencode_config.write_text(json.dumps({
        "mcp": {"github": {"enabled": True}},
        "permission": {}, "agent": {"coder": {"permission": {}}},
    }))
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "coder.md").write_text("bounded coder")
    args = type("Args", (), {
        "opencode_config": opencode_config, "opencode_agents": agents,
        "qwen_api_key": "not-needed", "qwen_base_url": "http://localhost/v1",
        "model": "vllm/Qwen/Test",
    })()
    open_attempt = tmp_path / "open"
    open_env = RUNTIME_MODULE.opencode_environment(args, open_attempt)
    rendered = json.loads((Path(open_env["XDG_CONFIG_HOME"]) / "opencode/opencode.json").read_text())
    assert rendered["mcp"]["github"]["enabled"] is False
    assert rendered["permission"]["external_directory"] == "deny"
    assert rendered["agent"]["coder"]["permission"]["task"] == "deny"
    assert (Path(open_env["XDG_CONFIG_HOME"]) / "opencode/agents/coder.md").read_text() == "bounded coder"

    qwen_attempt = tmp_path / "qwen"
    qwen_env = RUNTIME_MODULE.qwen_environment(args, qwen_attempt)
    settings = json.loads((Path(qwen_env["QWEN_CONFIG_DIR"]) / "settings.json").read_text())
    assert settings["privacy"]["usageStatisticsEnabled"] is False
    assert "Agent" in settings["permissions"]["deny"]


def test_native_runtime_environments_exclude_behavioral_agents(tmp_path: Path):
    args = type("Args", (), {
        "qwen_api_key": "not-needed", "qwen_base_url": "http://localhost:8000/v1",
        "model": "vllm/Qwen/Test", "reasoning_effort": None,
    })()
    open_env = RUNTIME_MODULE.native_opencode_environment(args, tmp_path / "open")
    open_config = json.loads(RUNTIME_MODULE.effective_config_path("opencode", open_env).read_text())
    assert "agent" not in open_config
    assert open_config["provider"]["vllm"]["models"] == {"Qwen/Test": {"name": "Qwen/Test"}}
    assert open_config["permission"] == {"external_directory": "deny"}

    qwen_env = RUNTIME_MODULE.native_qwen_environment(args, tmp_path / "qwen")
    qwen_config = json.loads(RUNTIME_MODULE.effective_config_path("qwen-code", qwen_env).read_text())
    assert qwen_config == {
        "privacy": {"usageStatisticsEnabled": False},
        "telemetry": {"enabled": False},
    }

    args.reasoning_effort = "xhigh"
    thinking_env = RUNTIME_MODULE.native_qwen_environment(args, tmp_path / "thinking")
    thinking_config = json.loads(RUNTIME_MODULE.effective_config_path("qwen-code", thinking_env).read_text())
    assert thinking_config["model"] == {
        "generationConfig": {"extra_body": {
            "chat_template_kwargs": {
                "enable_thinking": True, "preserve_thinking": True,
                "reasoning_effort": "xhigh",
            },
        }},
    }


def test_native_boundary_is_neutral_and_short():
    boundary = RUNTIME_MODULE.NATIVE_BOUNDARY.read_text()
    assert len(boundary) < 250
    for behavioral_word in ("plan", "test", "report", "promptly", "workflow"):
        assert behavioral_word not in boundary.lower()


def test_resume_identity_rejects_mode_boundary_and_config_changes():
    args = type("Args", (), {
        "model": "vllm/Qwen/Test", "runtime": "opencode", "mode": "native",
        "runtime_config_hash": "config-a", "timeout_multiplier": 1.0,
    })()
    ids = {"identity_hash": "fixture-a"}
    old = {
        "identity_hash": "fixture-a", "model": args.model, "runtime": args.runtime,
        "mode": args.mode, "boundary_hash": "boundary-a",
        "runtime_config_hash": args.runtime_config_hash, "terminal": True,
        "timeout_multiplier": 1.0,
    }
    assert RUNTIME_MODULE.resume_matches(old, args, ids, "boundary-a")
    for key, value in (
        ("mode", "tailored"), ("boundary_hash", "boundary-b"),
        ("runtime_config_hash", "config-b"), ("terminal", False),
        ("timeout_multiplier", 4.0),
    ):
        changed = dict(old)
        changed[key] = value
        assert not RUNTIME_MODULE.resume_matches(changed, args, ids, "boundary-a")


def test_native_review_prompt_and_digest_ignore_only_review_artifact(tmp_path: Path):
    (tmp_path / "app.py").write_text("value = 1\n")
    before = REVIEW_MODULE.implementation_digest(tmp_path)
    (tmp_path / "REVIEW.json").write_text('{"verdict":"APPROVE"}\n')
    assert REVIEW_MODULE.implementation_digest(tmp_path) == before
    (tmp_path / "app.py").write_text("value = 2\n")
    assert REVIEW_MODULE.implementation_digest(tmp_path) != before
    prompt = REVIEW_MODULE.review_prompt("Fix the value")
    assert "APPROVE" in prompt and "BLOCK" in prompt


def test_compare_preserves_routes_until_all_ten_identity_matched_pairs(tmp_path: Path, capsys):
    args = type("Args", (), {"manifest":ROOT / "manifest.json", "output":tmp_path, "left":"left", "right":"right"})()
    assert MODULE.command_compare(args) == 2
    comparison = json.loads((tmp_path / "comparison.json").read_text())
    assert comparison["recommendation"]["current_routes_preserved"] is True
    assert comparison["recommendation"]["evidence_complete"] is False


def test_all_baselines_fail_their_hidden_oracle():
    for case in json.loads((ROOT / "manifest.json").read_text())["cases"]:
        result = subprocess.run(
            [sys.executable, str(ROOT / "oracle.py"), case["id"], str(ROOT / "input")],
            text=True, capture_output=True,
        )
        assert result.returncode != 0, case["id"]


def test_policy_selection_is_correctness_first_and_non_regressing(tmp_path: Path, capsys):
    for variant, elapsed, tools, tokens in (("baseline", 10, 5, 1000), ("compact", 8, 4, 900)):
        for index in range(10):
            path = tmp_path / "attempts" / variant / str(index)
            path.mkdir(parents=True)
            (path / "attempt.json").write_text(json.dumps({
                "policy_variant": variant, "deterministic_correct": True,
                "state": "completed", "model": "coder-next",
                "elapsed_seconds": elapsed, "tool_calls": tools, "tool_errors": 0,
                "tokens_total": tokens, "corrections": 0,
            }))
    args = type("Args", (), {"output": tmp_path, "baseline": "baseline", "candidate": "compact", "model": "coder-next"})()
    assert MODULE.command_select(args) == 0
    decision = json.loads((tmp_path / "policy-selection.json").read_text())
    assert decision["promoted"] is True
    assert decision["decision"] == "compact"

    broken = tmp_path / "attempts" / "compact" / "0" / "attempt.json"
    payload = json.loads(broken.read_text())
    payload["deterministic_correct"] = False
    broken.write_text(json.dumps(payload))
    assert MODULE.command_select(args) == 2
    assert json.loads((tmp_path / "policy-selection.json").read_text())["promoted"] is False
