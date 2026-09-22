import json
import os
import subprocess
from pathlib import Path

import pytest

from codex_local_harness.completion import acknowledge, snapshot
from codex_local_harness.supervision import finish, guidance, observe, register


ROOT = Path(__file__).resolve().parents[1]


def admission(tmp_path):
    return dict(run_id="run-1", session_id="owner", repository="example/project", issue=181,
                role="coder", checkout=str(tmp_path / "checkout"), source=str(tmp_path),
                log=str(tmp_path / "run.jsonl"))


def test_admission_survives_missing_workflow_and_requires_terminal_exit(tmp_path):
    register(tmp_path, admission(tmp_path))
    assert snapshot(tmp_path, "owner")[0]["state"] == "pending"
    assert snapshot(tmp_path, "other") == []
    with pytest.raises(ValueError):
        acknowledge(tmp_path, "owner", "run-1", "not finished")
    finish(tmp_path, "run-1", "owner", 137)
    result = acknowledge(tmp_path, "owner", "run-1", "failure inspected; retained work to recover")
    assert result["state"] == "failed"
    assert result["exit_status"] == 137
    with pytest.raises(ValueError):
        finish(tmp_path, "run-1", "other", 0)


def test_observation_is_incremental_retries_partial_lines_and_omits_content(tmp_path):
    log = tmp_path / "run.jsonl"
    first = json.dumps({"type": "tool_use", "part": {"state": {"status": "error", "input": "private"}}}) + "\n"
    log.write_text(first + '{"type":')
    delta = observe(log)
    assert len(delta["events"]) == 1
    assert "private" not in json.dumps(delta)
    assert observe(log, delta["offset"])["events"] == []
    with log.open("a") as stream:
        stream.write('"result"}\n')
    assert observe(log, delta["offset"])["events"][0]["type"] == "result"


def test_guidance_is_queued_not_falsely_claimed_delivered(tmp_path):
    register(tmp_path, admission(tmp_path))
    task = tmp_path / "advice.txt"
    task.write_text("Check physical actuator position rather than commanded position.")
    result = guidance(tmp_path, "run-1", "owner", task)
    assert result["state"] == "queued-for-checkpoint-restart"
    assert Path(result["path"]).read_text() == task.read_text()
    assert guidance(tmp_path, "run-1", "owner", task) == result
    with pytest.raises(ValueError):
        guidance(tmp_path, "run-1", "other", task)


def test_candidate_timeout_preserves_unpublished_edit_and_records_ownership(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-b", "issue-181-smoke", str(repo)], check=True, capture_output=True)
    for key, value in [("user.name", "Test"), ("user.email", "test@example.invalid")]:
        subprocess.run(["git", "-C", str(repo), "config", key, value], check=True)
    (repo / "value.txt").write_text("original\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", "https://github.com/example/project.git"], check=True)
    fake = tmp_path / "opencode"
    fake.write_text('#!/bin/sh\nwhile [ $# -gt 0 ]; do [ "$1" = --dir ] && { shift; target=$1; }; shift; done\nprintf "retained\\n" > "$target/value.txt"\nsleep 5\n')
    fake.chmod(0o755)
    routes = tmp_path / "routes.json"
    routes.write_text(json.dumps({"defaults": {"coder": "standard"}, "routes": {
        "coder/standard": {"agent": "coder", "model": "vllm/test", "runtime": "opencode", "mode": "native"}}}))
    health = tmp_path / "health"
    env = os.environ | {"CODEX_LOCAL_WORKER_CONFIG": str(routes), "CODEX_LOCAL_OPENCODE": str(fake),
        "CODEX_LOCAL_MODEL_SWITCH": "0", "CODEX_LOCAL_SANDBOX": "disabled", "CODEX_LOCAL_ALLOW_UNSANDBOXED": "1",
        "CODEX_LOCAL_RECORD_RUN": "1", "CODEX_THREAD_ID": "owner", "CODEX_LOCAL_SESSION_ID": "owner",
        "CODEX_LOCAL_REPOSITORY": "example/project", "CODEX_LOCAL_ISSUE": "181",
        "CODEX_LOCAL_TIMEOUT_SECONDS": "1", "CODEX_LOCAL_HEALTH_DIR": str(health),
        "CODEX_LOCAL_LOG_DIR": str(tmp_path / "logs"), "CODEX_LOCAL_LEGACY_LEDGER": "/bin/true",
        "CODEX_LOCAL_ISOLATION_PARENT": str(tmp_path / "isolated"), "CODEX_LOCAL_CODER_POSTFLIGHT": "0"}
    result = subprocess.run([str(ROOT / "bin/codex-local-worker-candidate"), "coder", str(repo), "edit"],
                            env=env, text=True, capture_output=True, timeout=15)
    assert result.returncode == 124, result.stderr
    assert (repo / "value.txt").read_text() == "original\n"
    item = json.loads((health / "ownership.jsonl").read_text())
    assert (Path(item["checkout"]) / "value.txt").read_text() == "retained\n"
    assert snapshot(health, "owner")[0]["state"] == "failed"


    # Resume the very same unpublished tree with no whole-task deadline. Guidance
    # is attached to the new prompt; a model-emitted receipt is recorded separately.
    acknowledge(health, "owner", item["run_id"], "Inspected timeout and retained edit; resume smoke")
    advice = tmp_path / "advice.txt"
    advice.write_text("Preserve the edit and create second.txt.")
    queued = guidance(health, item["run_id"], "owner", advice)
    fake.write_text('#!/bin/sh\nwhile [ $# -gt 0 ]; do [ "$1" = --dir ] && { shift; target=$1; }; shift; done\n'
                    'test "$(cat "$target/value.txt")" = retained || exit 9\n'
                    'printf "continued\\n" > "$target/second.txt"\nsleep 2\n'
                    f"printf '%s\\n' '{{\"type\":\"text\",\"part\":{{\"text\":\"GUIDANCE_RECEIPT={queued['guidance_id']}\"}}}}'\n")
    resumed = subprocess.run([str(ROOT / "bin/codex-local-worker-candidate"), "coder", str(repo), "Continue the scoped task."],
        env=env | {"CODEX_LOCAL_TIMEOUT_SECONDS": "0", "CODEX_LOCAL_RESUME_RUN": item["run_id"]},
        text=True, capture_output=True, timeout=15)
    assert resumed.returncode == 0, resumed.stderr
    assert (Path(item["checkout"]) / "second.txt").read_text() == "continued\n"
    evidence = [json.loads(line) for line in (health / "guidance.jsonl").read_text().splitlines()]
    assert evidence[-1]["state"] == "model-reported-receipt"


def test_cancel_rejects_unisolated_process_group(tmp_path, monkeypatch):
    from codex_local_harness.supervision import cancel
    register(tmp_path, admission(tmp_path))
    monkeypatch.setattr("codex_local_harness.supervision.os.getpgid", lambda pid: -1)
    monkeypatch.setattr("codex_local_harness.supervision.os.killpg", lambda *args: pytest.fail("must not signal an unowned group"))
    with pytest.raises(ValueError, match="process group"):
        cancel(tmp_path, "run-1", "owner")


def test_malformed_optional_result_does_not_hide_execution_completion(tmp_path):
    register(tmp_path, admission(tmp_path))
    (tmp_path / "run.result.json").write_text('{"result":')
    finish(tmp_path, "run-1", "owner", 65)
    result = snapshot(tmp_path, "owner")[0]
    assert result["state"] == "failed"
    terminal = json.loads((tmp_path / "execution.jsonl").read_text())
    assert terminal["result_evidence_error"] == "JSONDecodeError"
    assert terminal["acceptance"] == "unknown"


def test_frontier_assessment_uses_deltas_and_retains_unknowns(tmp_path, monkeypatch):
    from codex_local_harness import assessment_usage as usage
    register(tmp_path, admission(tmp_path))
    values = dict(input_tokens=100, cached_input_tokens=80, output_tokens=20,
                  reasoning_output_tokens=10, total_tokens=120)
    monkeypatch.setattr(usage, "counters", lambda *_: {"counters": values.copy()})
    usage.capture(tmp_path, tmp_path, "owner", "run-1", "a", "start", "monitoring")
    values.update(input_tokens=150, cached_input_tokens=120, output_tokens=25,
                  reasoning_output_tokens=12, total_tokens=175)
    result = usage.capture(tmp_path, tmp_path, "owner", "run-1", "a", "finish", "monitoring")
    assert result["status"] == "observed"
    assert result["delta"]["total_tokens"] == 55  # no double-added reasoning
    with pytest.raises(ValueError, match="already captured"):
        usage.capture(tmp_path, tmp_path, "owner", "run-1", "a", "finish", "monitoring")
    result = usage.capture(tmp_path, tmp_path, "owner", "run-1", "b", "finish", "guidance")
    assert result["status"] == "unknown" and result["delta"] is None
    usage.capture(tmp_path, tmp_path, "owner", "run-1", "c", "start", "recovery")
    values["total_tokens"] = 1
    result = usage.capture(tmp_path, tmp_path, "owner", "run-1", "c", "finish", "recovery")
    assert result["diagnostic"] == "counter-reset-or-inconsistent-cache"


def test_frontier_snapshot_accepts_current_schema_without_inventing_cache_writes(tmp_path):
    from codex_local_harness.assessment_usage import counters
    session = "00000000-0000-0000-0000-000000000001"
    path = tmp_path / f"rollout-test-{session}.jsonl"
    values = dict(input_tokens=10, cached_input_tokens=8, output_tokens=3,
                  reasoning_output_tokens=1, total_tokens=13)
    path.write_text(json.dumps({"payload": {"type": "token_count", "info": {
        "total_token_usage": values}}, "timestamp": "observed"}) + "\n")
    assert counters(tmp_path, session)["counters"] == values


def test_reviewer_allowances_follow_deny_in_final_agent_layer(tmp_path):
    from codex_local_harness.review_profile import render
    template = (ROOT / "agents/convergence-reviewer.md").read_text()
    checkout, scratch = tmp_path / "checkout", tmp_path / 'scratch "quoted"'
    agent = render(template, checkout, scratch)
    line = next(line for line in agent.splitlines() if line.startswith("  external_directory:"))
    rules = json.loads(line.split(":", 1)[1])
    assert list(rules)[0] == "*" and rules["*"] == "deny"
    assert rules[str(scratch)] == rules[str(scratch) + "/*"] == "allow"
    assert rules[str(checkout)] == "allow"


def test_candidate_summary_does_not_claim_zero_frontier_spend():
    from codex_local_harness.health import RunHealth, summarize
    from codex_local_harness.monitor import assess
    from codex_local_harness.read_model import build_run_views, summarize_views
    run = RunHealth(run_id="candidate", issue=181, role="coder", runtime="qwen-code",
                    model="test", profile="candidate", task_class="test", status="completed",
                    wall_seconds=1, intervention_depth="unknown",
                    canonical_usage={"coverage": "complete", "total_including_cache": 100})
    assert run.as_dict()["usage"]["frontier_total"] is None
    summary = summarize([run])
    assert summary["frontier_tokens"] is None and summary["frontier_token_share"] is None
    assert assess(summary)["status"] == "insufficient-data"
    assert summarize_views(build_run_views([run]))["frontier_tokens"] is None


def test_correction_handoff_rejects_stale_head_and_queues_exact_head(tmp_path):
    from codex_local_harness.supervision import correction
    repo = tmp_path / "checkout"
    subprocess.run(["git", "init", "-b", "issue-181", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "--allow-empty", "-m", "fixture"], check=True, capture_output=True)
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    register(tmp_path, admission(tmp_path))
    finish(tmp_path, "run-1", "owner", 0)
    result = {"schema_version": 1, "repository": "example/project", "issue": 181,
              "pull_request": 183, "reviewed_head_sha": "a" * 40, "verdict": "REQUEST_CHANGES",
              "criteria": ["Reject non-finite queries"], "findings": [{"severity": "high", "detail": "NaN returns None"}],
              "tests": [], "confidence": "high", "summary": "Contract mismatch", "escalation_reason": None}
    artifact = tmp_path / "review.json"
    artifact.write_text(json.dumps({"result": result}))
    with pytest.raises(ValueError, match="head"):
        correction(tmp_path, "run-1", "owner", artifact)
    result["reviewed_head_sha"] = head
    artifact.write_text(json.dumps({"result": result}))
    event = correction(tmp_path, "run-1", "owner", artifact)
    assert event["state"] == "queued-for-checkpoint-restart"
    assert "NaN returns None" in Path(event["path"]).read_text()
