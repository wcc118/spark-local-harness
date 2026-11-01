"""Tests for ToolCallBudget class and CLI."""
import json
import pytest
from pathlib import Path

from codex_local_harness.tool_budget import ToolCallBudget, main


class TestToolCallBudgetInit:
    """Test ToolCallBudget initialization validation."""

    def test_valid_positive_integer_limit(self):
        budget = ToolCallBudget(10)
        assert budget.count == 0

    def test_zero_limit_raises(self):
        with pytest.raises(ValueError, match="positive"):
            ToolCallBudget(0)

    def test_negative_limit_raises(self):
        with pytest.raises(ValueError, match="positive"):
            ToolCallBudget(-5)

    def test_bool_limit_raises(self):
        with pytest.raises(ValueError, match="positive"):
            ToolCallBudget(True)

    def test_string_limit_raises(self):
        with pytest.raises(ValueError, match="positive"):
            ToolCallBudget("10")

    def test_float_limit_raises(self):
        with pytest.raises(ValueError, match="positive"):
            ToolCallBudget(10.5)


class TestToolCallBudgetObserve:
    """Test ToolCallBudget.observe() method."""

    def test_empty_event_ignored(self):
        budget = ToolCallBudget(5)
        budget.observe({})
        assert budget.count == 0

    def test_non_dict_event_ignored(self):
        budget = ToolCallBudget(5)
        budget.observe(None)
        budget.observe("string")
        budget.observe([1, 2, 3])
        assert budget.count == 0

    def test_opencode_tool_use_completed_nonempty(self):
        budget = ToolCallBudget(5)
        event = {
            "type": "tool_use",
            "part": {
                "callID": "call-1",
                "tool": "bash",
                "state": {"status": "completed", "input": {"command": "echo hello"}}
            }
        }
        budget.observe(event)
        assert budget.count == 1

    def test_opencode_tool_use_completed_empty_command(self):
        budget = ToolCallBudget(5)
        event = {
            "type": "tool_use",
            "part": {
                "callID": "call-1",
                "tool": "bash",
                "state": {"status": "completed", "input": {"command": "   "}}
            }
        }
        budget.observe(event)
        assert budget.count == 0

    def test_opencode_tool_use_completed_whitespace_only_command(self):
        budget = ToolCallBudget(5)
        event = {
            "type": "tool_use",
            "part": {
                "callID": "call-1",
                "tool": "bash",
                "state": {"status": "completed", "input": {"command": ""}}
            }
        }
        budget.observe(event)
        assert budget.count == 0

    def test_opencode_tool_use_completed_no_command_key(self):
        budget = ToolCallBudget(5)
        event = {
            "type": "tool_use",
            "part": {
                "callID": "call-1",
                "tool": "bash",
                "state": {"status": "completed", "input": {"other_key": "value"}}
            }
        }
        budget.observe(event)
        # No command key = no command specified = empty input
        assert budget.count == 0

    def test_opencode_tool_use_error_still_counts(self):
        budget = ToolCallBudget(5)
        event = {
            "type": "tool_use",
            "part": {
                "callID": "call-1",
                "tool": "bash",
                "state": {"status": "error", "input": {"command": "echo test"}}
            }
        }
        budget.observe(event)
        assert budget.count == 1

    def test_opencode_tool_use_pending_ignored(self):
        budget = ToolCallBudget(5)
        event = {
            "type": "tool_use",
            "part": {
                "callID": "call-1",
                "tool": "bash",
                "state": {"status": "pending", "input": {"command": "echo test"}}
            }
        }
        budget.observe(event)
        assert budget.count == 0

    def test_opencode_tool_use_running_ignored(self):
        budget = ToolCallBudget(5)
        event = {
            "type": "tool_use",
            "part": {
                "callID": "call-1",
                "tool": "bash",
                "state": {"status": "running", "input": {"command": "echo test"}}
            }
        }
        budget.observe(event)
        assert budget.count == 0

    def test_opencode_tool_result_counts_as_completion(self):
        budget = ToolCallBudget(5)
        # First track pending call
        budget.observe({
            "type": "tool_use",
            "part": {
                "callID": "call-1",
                "tool": "bash",
                "state": {"status": "pending", "input": {"command": "echo test"}}
            }
        })
        assert budget.count == 0
        # Then observe result
        budget.observe({
            "type": "tool_result",
            "tool_use_id": "call-1"
        })
        assert budget.count == 1

    def test_opencode_tool_result_with_part_counts(self):
        budget = ToolCallBudget(5)
        budget.observe({
            "type": "tool_use",
            "part": {
                "callID": "call-2",
                "tool": "bash",
                "state": {"status": "running", "input": {"command": "echo test"}}
            }
        })
        budget.observe({
            "type": "tool_result",
            "part": {
                "callID": "call-2",
                "state": {"status": "completed"}
            }
        })
        assert budget.count == 1

    def test_qwen_native_tool_use_counts(self):
        budget = ToolCallBudget(5)
        budget.observe({
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tooluse-1",
                        "name": "bash",
                        "input": {"command": "echo hello"}
                    }
                ]
            }
        })
        # tool_use only records issuance, count is 0 until tool_result
        assert budget.count == 0

    def test_qwen_native_tool_result_counts(self):
        budget = ToolCallBudget(5)
        # First track pending
        budget.observe({
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tooluse-1",
                        "name": "bash",
                        "input": {"command": "echo hello"}
                    }
                ]
            }
        })
        # Then count on result
        budget.observe({
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tooluse-1"
                    }
                ]
            }
        })
        assert budget.count == 1

    def test_distinct_callids_count_separately(self):
        budget = ToolCallBudget(5)
        for i in range(3):
            budget.observe({
                "type": "tool_use",
                "part": {
                    "callID": f"call-{i}",
                    "tool": "bash",
                    "state": {"status": "completed", "input": {"command": "echo hello"}}
                }
            })
        assert budget.count == 3

    def test_identical_command_different_callids_counts_as_distinct(self):
        budget = ToolCallBudget(5)
        # Same command in different calls should count as 3
        for i in range(3):
            budget.observe({
                "type": "tool_use",
                "part": {
                    "callID": f"distinct-{i}",
                    "tool": "bash",
                    "state": {"status": "completed", "input": {"command": "echo hello"}}
                }
            })
        assert budget.count == 3

    def test_repeated_update_same_call_only_counts_once(self):
        budget = ToolCallBudget(5)
        event = {
            "type": "tool_use",
            "part": {
                "callID": "call-1",
                "tool": "bash",
                "state": {"status": "completed", "input": {"command": "echo hello"}}
            }
        }
        # Multiple observes of same call ID should only count once
        budget.observe(event)
        budget.observe(event)
        budget.observe(event)
        assert budget.count == 1

    def test_input_none_counts_as_empty(self):
        budget = ToolCallBudget(5)
        event = {
            "type": "tool_use",
            "part": {
                "callID": "call-1",
                "tool": "bash",
                "state": {"status": "completed", "input": None}
            }
        }
        budget.observe(event)
        assert budget.count == 0

    def test_exactly_at_limit_not_exceeded(self):
        budget = ToolCallBudget(3)
        for i in range(3):
            budget.observe({
                "type": "tool_use",
                "part": {
                    "callID": f"call-{i}",
                    "tool": "bash",
                    "state": {"status": "completed", "input": {"command": "echo test"}}
                }
            })
        assert budget.count == 3
        assert not budget.exceeded

    def test_one_over_limit_is_exceeded(self):
        budget = ToolCallBudget(3)
        for i in range(4):
            budget.observe({
                "type": "tool_use",
                "part": {
                    "callID": f"call-{i}",
                    "tool": "bash",
                    "state": {"status": "completed", "input": {"command": "echo test"}}
                }
            })
        assert budget.count == 4
        assert budget.exceeded

    def test_exceeded_property_up_to_date(self):
        budget = ToolCallBudget(2)
        assert not budget.exceeded
        budget.observe({
            "type": "tool_use",
            "part": {"callID": "c1", "tool": "bash", "state": {"status": "completed", "input": {"command": "a"}}}
        })
        assert not budget.exceeded
        budget.observe({
            "type": "tool_use",
            "part": {"callID": "c2", "tool": "bash", "state": {"status": "completed", "input": {"command": "b"}}}
        })
        assert not budget.exceeded
        budget.observe({
            "type": "tool_use",
            "part": {"callID": "c3", "tool": "bash", "state": {"status": "completed", "input": {"command": "c"}}}
        })
        assert budget.exceeded


class TestToolCallBudgetCLI:
    """Test CLI entry point."""

    def test_cli_valid_log_within_budget(self, tmp_path: Path):
        log_file = tmp_path / "test.jsonl"
        log_file.write_text('{"type":"tool_use","part":{"callID":"c1","tool":"bash","state":{"status":"completed","input":{"command":"echo hi"}}}}\n')
        result = main(["--log", str(log_file), "--limit", "5"])
        assert result == 0

    def test_cli_valid_log_exceeds_budget(self, tmp_path: Path):
        log_file = tmp_path / "test.jsonl"
        lines = [
            '{"type":"tool_use","part":{"callID":"c1","tool":"bash","state":{"status":"completed","input":{"command":"a"}}}}',
            '{"type":"tool_use","part":{"callID":"c2","tool":"bash","state":{"status":"completed","input":{"command":"b"}}}}',
            '{"type":"tool_use","part":{"callID":"c3","tool":"bash","state":{"status":"completed","input":{"command":"c"}}}}',
            '{"type":"tool_use","part":{"callID":"c4","tool":"bash","state":{"status":"completed","input":{"command":"d"}}}}',
            '{"type":"tool_use","part":{"callID":"c5","tool":"bash","state":{"status":"completed","input":{"command":"e"}}}}',
            '{"type":"tool_use","part":{"callID":"c6","tool":"bash","state":{"status":"completed","input":{"command":"f"}}}}',
        ]
        log_file.write_text("\n".join(lines) + "\n")
        result = main(["--log", str(log_file), "--limit", "5"])
        assert result == 75

    def test_cli_malformed_json(self, tmp_path: Path):
        log_file = tmp_path / "malformed.jsonl"
        log_file.write_text('not valid json\n')
        result = main(["--log", str(log_file), "--limit", "5"])
        assert result == 2

    def test_cli_zero_limit(self, tmp_path: Path):
        log_file = tmp_path / "test.jsonl"
        log_file.write_text('{}\n')
        result = main(["--log", str(log_file), "--limit", "0"])
        assert result == 2

    def test_cli_negative_limit(self, tmp_path: Path):
        log_file = tmp_path / "test.jsonl"
        log_file.write_text('{}\n')
        result = main(["--log", str(log_file), "--limit", "-1"])
        assert result == 2

    def test_cli_nonexistent_log(self, tmp_path: Path):
        log_file = tmp_path / "nonexistent.jsonl"
        result = main(["--log", str(log_file), "--limit", "5"])
        assert result == 2

    def test_cli_empty_bash_ignored(self, tmp_path: Path):
        log_file = tmp_path / "test.jsonl"
        log_file.write_text('{"type":"tool_use","part":{"callID":"c1","tool":"bash","state":{"status":"completed","input":{"command":"   "}}}}\n')
        log_file.write_text('{"type":"tool_use","part":{"callID":"c2","tool":"bash","state":{"status":"completed","input":{"command":""}}}}\n')
        log_file.write_text('{"type":"tool_use","part":{"callID":"c3","tool":"bash","state":{"status":"completed","input":{"command":"echo"}}}}\n')
        result = main(["--log", str(log_file), "--limit", "1"])
        assert result == 0

    def test_cli_distinct_commands_count_separately(self, tmp_path: Path):
        log_file = tmp_path / "test.jsonl"
        lines = [f'{{"type":"tool_use","part":{{"callID":"c{i}","tool":"bash","state":{{"status":"completed","input":{{"command":"echo {i}"}}}}}}}}' for i in range(5)]
        log_file.write_text("\n".join(lines) + "\n")
        result = main(["--log", str(log_file), "--limit", "4"])
        assert result == 75

    def test_cli_pending_not_counted(self, tmp_path: Path):
        log_file = tmp_path / "test.jsonl"
        log_file.write_text('{"type":"tool_use","part":{"callID":"c1","tool":"bash","state":{"status":"pending","input":{"command":"echo"}}}}\n')
        log_file.write_text('{"type":"tool_use","part":{"callID":"c2","tool":"bash","state":{"status":"running","input":{"command":"echo"}}}}\n')
        log_file.write_text('{"type":"tool_use","part":{"callID":"c3","tool":"bash","state":{"status":"completed","input":{"command":"echo"}}}}\n')
        result = main(["--log", str(log_file), "--limit", "1"])
        assert result == 0

    def test_cli_error_status_counts(self, tmp_path: Path):
        log_file = tmp_path / "test.jsonl"
        log_file.write_text('{"type":"tool_use","part":{"callID":"c1","tool":"bash","state":{"status":"error","input":{"command":"echo"}}}}\n')
        result = main(["--log", str(log_file), "--limit", "1"])
        assert result == 0

    def test_cli_qwen_native_format(self, tmp_path: Path):
        log_file = tmp_path / "test.jsonl"
        log_file.write_text('{"message":{"content":[{"type":"tool_use","id":"t1","name":"bash","input":{"command":"echo"}}]}}\n')
        result = main(["--log", str(log_file), "--limit", "1"])
        assert result == 0

    def test_cli_tool_result_counts(self, tmp_path: Path):
        log_file = tmp_path / "test.jsonl"
        log_file.write_text('{"type":"tool_use","part":{"callID":"c1","tool":"bash","state":{"status":"pending","input":{"command":"echo"}}}}\n')
        log_file.write_text('{"type":"tool_result","tool_use_id":"c1"}\n')
        result = main(["--log", str(log_file), "--limit", "1"])
        assert result == 0
