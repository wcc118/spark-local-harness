from codex_local_harness.diagnostics import read_records
from codex_local_harness.frontier import FrontierStatus
from codex_local_harness.observe_cli import main
from codex_local_harness.workflow import WorkflowEvent


def test_cli_appends_workflow_and_unavailable_frontier_facts(tmp_path, capsys):
    workflow = tmp_path / "workflow.jsonl"
    frontier = tmp_path / "frontier.jsonl"
    assert main(["workflow", "--records", str(workflow), "--event-id", "evt-1", "--operation-id", "issue-37", "--stage", "local-work", "--state", "active", "--observed-at", "2026-08-15T18:00:00Z", "--issue", "37", "--role", "coder", "--model", "qwen"]) == 0
    assert main(["frontier", "--records", str(frontier), "--status-id", "status-1", "--observed-at", "2026-08-15T18:00:00Z", "--source", "unavailable", "--note-code", "official-interface-unavailable"]) == 0
    assert read_records(workflow, WorkflowEvent)[0].state == "active"
    assert read_records(frontier, FrontierStatus)[0].source == "unavailable"
    assert len(capsys.readouterr().out.splitlines()) == 2
