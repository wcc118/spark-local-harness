import json
import os
import subprocess
from pathlib import Path

from codex_local_harness.diagnostics import LifecycleEvent, read_records
from codex_local_harness.health import read_jsonl
from codex_local_harness.workflow import WorkflowEvent, derive_operations


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "bin" / "codex-local-worker-v4"


def _worker_environment(tmp_path, fake_opencode):
    config = tmp_path / "workers.json"
    config.write_text(json.dumps({
        "defaults": {"helper": "standard"},
        "routes": {"helper/standard": {"agent": "helper", "model": "vllm/test"}},
    }), encoding="utf-8")
    return os.environ | {
        "CODEX_LOCAL_WORKER_CONFIG": str(config),
        "CODEX_LOCAL_OPENCODE": str(fake_opencode),
        "CODEX_LOCAL_RECORD_RUN": "1",
        "CODEX_LOCAL_REPOSITORY": "owner/repo",
        "CODEX_LOCAL_ISSUE": "53",
        "CODEX_LOCAL_TASK_CLASS": "routine",
        "CODEX_LOCAL_HEALTH_RECORDS": str(tmp_path / "runs.jsonl"),
        "CODEX_LOCAL_LIFECYCLE_RECORDS": str(tmp_path / "lifecycle.jsonl"),
        "CODEX_LOCAL_WORKFLOW_RECORDS": str(tmp_path / "workflow.jsonl"),
        "CODEX_LOCAL_PUBLICATION_FAILURE_RECORDS": str(tmp_path / "publication-failures.jsonl"),
        "CODEX_LOCAL_LEGACY_LEDGER": "/bin/true",
        "CODEX_LOCAL_SOURCE": str(ROOT / "src"),
    }


def _fake(tmp_path, body):
    path = tmp_path / "fake-opencode"
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_successful_helper_publishes_run_lifecycle_and_workflow(tmp_path):
    fake = _fake(tmp_path, "printf '%s\\n' '{\"type\":\"step_finish\",\"timestamp\":1000,\"part\":{\"tokens\":{\"input\":7,\"output\":2,\"reasoning\":1}}}' '{\"type\":\"text\",\"timestamp\":2000,\"part\":{\"text\":\"done\"}}'\n")
    env = _worker_environment(tmp_path, fake)
    repo = tmp_path / "repo"
    repo.mkdir()
    completed = subprocess.run([str(WORKER), "helper", str(repo), "inspect"], env=env, text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr
    run = read_jsonl(tmp_path / "runs.jsonl")[0]
    assert (run.run_id, run.issue, run.repository, run.usage.local_total) == (run.run_id, 53, "owner/repo", 10)
    lifecycle = read_records(tmp_path / "lifecycle.jsonl", LifecycleEvent)
    assert [event.event for event in lifecycle] == ["started", "completed"]
    operation = derive_operations(read_records(tmp_path / "workflow.jsonl", WorkflowEvent))[0]
    assert (operation["current_stage"], operation["current_state"], operation["run_id"]) == ("evidence", "passed", run.run_id)


def test_timeout_is_published_as_interrupted_without_claiming_success(tmp_path):
    fake = _fake(tmp_path, "sleep 5\n")
    env = _worker_environment(tmp_path, fake) | {"CODEX_LOCAL_TIMEOUT_SECONDS": "1"}
    repo = tmp_path / "repo"
    repo.mkdir()
    completed = subprocess.run([str(WORKER), "helper", str(repo), "inspect"], env=env, text=True, capture_output=True)
    assert completed.returncode == 124
    assert read_jsonl(tmp_path / "runs.jsonl")[0].status == "timed-out"
    assert read_records(tmp_path / "lifecycle.jsonl", LifecycleEvent)[-1].event == "timed-out"
    operation = derive_operations(read_records(tmp_path / "workflow.jsonl", WorkflowEvent))[0]
    assert (operation["current_state"], operation["stop_reason"]) == ("interrupted", "timed-out")


def test_malformed_output_still_publishes_failed_run(tmp_path):
    fake = _fake(tmp_path, "printf 'not-json\\n'\n")
    env = _worker_environment(tmp_path, fake)
    repo = tmp_path / "repo"
    repo.mkdir()
    completed = subprocess.run([str(WORKER), "helper", str(repo), "inspect"], env=env, text=True, capture_output=True)
    assert completed.returncode == 65
    run = read_jsonl(tmp_path / "runs.jsonl")[0]
    assert (run.status, run.failure_class, run.usage.local_total) == ("failure", "malformed-result", 0)


def test_all_worker_roles_have_explicit_workflow_stage_mapping():
    content = WORKER.read_text(encoding="utf-8")
    assert "coder) worker_workflow_stage=local-work" in content
    assert "reviewer) worker_workflow_stage=review" in content
    assert "helper) worker_workflow_stage=evidence" in content
