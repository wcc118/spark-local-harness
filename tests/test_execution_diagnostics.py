import json

import pytest

from codex_local_harness.execution_diagnostics import ExecutionDiagnostics, load_stage_budgets


def tool(call_id, tool="read", **inputs):
    return {"type": "tool_use", "part": {"callID": call_id, "tool": tool, "state": {
        "status": "completed", "input": inputs,
    }}}


def test_repeated_completed_command_is_fingerprinted_without_raw_input():
    detector = ExecutionDiagnostics("coder", load_stage_budgets(None), clock=lambda: 0)
    output = []
    for index in range(3):
        output += detector.observe(tool(str(index), filePath="/secret/repository/file.py"))
    signal = next(item for item in output if item["part"]["kind"] == "exploration-loop")
    assert signal["part"]["count"] == 3
    assert "/secret" not in json.dumps(signal)


def test_state_updates_for_one_call_do_not_look_like_a_loop():
    detector = ExecutionDiagnostics("coder", load_stage_budgets(None), clock=lambda: 0)
    assert not detector.observe(tool("same", filePath="file.py"))
    assert not detector.observe(tool("same", filePath="file.py"))
    assert not detector.observe(tool("same", filePath="file.py"))


def test_stage_transition_and_budget_warning_are_observable():
    budgets = load_stage_budgets('{"implementation":{"tool_calls":1}}')
    detector = ExecutionDiagnostics("coder", budgets, clock=lambda: 0)
    output = detector.observe(tool("edit-1", "edit", filePath="app.py"))
    assert [item["part"]["kind"] for item in output] == ["stage-transition", "stage-budget"]
    assert output[-1]["part"]["severity"] == "warning"
    output = detector.observe(tool("edit-2", "edit", filePath="other.py"))
    assert output[-1]["part"]["severity"] == "critical"


def test_testing_stage_and_reviewer_budget():
    coder = ExecutionDiagnostics("coder", load_stage_budgets(None), clock=lambda: 0)
    assert coder.observe(tool("test", "bash", command="pytest -q"))[0]["part"]["stage"] == "testing"
    reviewer = ExecutionDiagnostics("reviewer", load_stage_budgets(None), clock=lambda: 0)
    signal = reviewer.observe(tool("read", "read", filePath="app.py"))[-1]
    assert signal["part"]["stage"] == "review"
    assert signal["part"]["severity"] == "critical"


@pytest.mark.parametrize("raw", ["[]", '{"unknown":{}}', '{"testing":{"tokens":-1}}'])
def test_invalid_budget_configuration_is_rejected(raw):
    with pytest.raises(ValueError):
        load_stage_budgets(raw)
