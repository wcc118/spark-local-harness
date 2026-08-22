from codex_local_harness.diagnostics import read_records
from codex_local_harness.frontier import FrontierStatus
from codex_local_harness.observe_cli import main
from codex_local_harness.workflow import WorkflowEvent


def test_cli_appends_workflow_and_unavailable_frontier_facts(tmp_path, capsys):
    workflow = tmp_path / "workflow.jsonl"
    frontier = tmp_path / "frontier.jsonl"
    assert main(["workflow", "--records", str(workflow), "--event-id", "evt-1", "--operation-id", "issue-37", "--stage", "local-work", "--state", "active", "--observed-at", "2026-08-15T18:00:00Z", "--issue", "37", "--role", "coder", "--model", "qwen"]) == 0
    assert main(["frontier", "--records", str(frontier), "--status-id", "status-1", "--observed-at", "2026-08-15T18:00:00Z", "--source", "unavailable", "--note-code", "official-interface-unavailable"]) == 0
    workflow_record = read_records(workflow, WorkflowEvent)[0]
    assert workflow_record.state == "active"
    assert (workflow_record.schema_version, workflow_record.actor, workflow_record.source) == (2, "worker", "worker-wrapper")
    assert (workflow_record.milestone, workflow_record.prior_milestone, workflow_record.activity_type) == ("implementation", None, None)
    assert read_records(frontier, FrontierStatus)[0].source == "unavailable"
    assert len(capsys.readouterr().out.splitlines()) == 2


def test_cli_uses_same_v2_contract_for_codex_and_attached_activity(tmp_path, capsys):
    records = tmp_path / "workflow.jsonl"
    common = ["workflow", "--records", str(records), "--operation-id", "issue-56", "--issue", "56", "--repository", "owner/repo", "--session-id", "thread-a"]
    assert main(common + ["--event-id", "codex-routing", "--stage", "routing", "--state", "passed", "--observed-at", "2026-08-16T01:00:00Z"]) == 0
    assert main(common + ["--event-id", "worker-evidence", "--stage", "evidence", "--state", "active", "--observed-at", "2026-08-16T01:01:00Z", "--role", "helper", "--run-id", "run-1"]) == 0
    codex, worker = read_records(records, WorkflowEvent)
    assert (codex.actor, codex.source, codex.milestone) == ("codex", "codex-cli", "routing-decision")
    assert (worker.actor, worker.source, worker.milestone, worker.prior_milestone, worker.activity_type) == (
        "worker", "worker-wrapper", "routing-decision", "routing-decision", "evidence",
    )


def test_cli_publishes_first_observed_reviewer_start_and_terminal_fact(tmp_path, capsys):
    records = tmp_path / "workflow.jsonl"
    common = [
        "workflow", "--records", str(records), "--operation-id", "issue-119",
        "--issue", "119", "--pull-request", "120", "--repository", "owner/repo",
        "--session-id", "thread-a", "--stage", "review", "--role", "reviewer",
        "--run-id", "review-run", "--model", "review-model",
    ]
    assert main(common + [
        "--event-id", "review-started", "--state", "active",
        "--observed-at", "2026-08-22T15:00:00Z",
    ]) == 0
    assert main(common + [
        "--event-id", "review-passed", "--state", "passed",
        "--observed-at", "2026-08-22T15:01:00Z",
    ]) == 0

    started, passed = read_records(records, WorkflowEvent)
    assert (started.stage, started.state, started.pull_request) == ("review", "active", 120)
    assert (passed.stage, passed.state, passed.prior_milestone) == ("review", "passed", "review")
    assert len(capsys.readouterr().out.splitlines()) == 2
