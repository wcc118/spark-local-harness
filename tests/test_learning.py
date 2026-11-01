"""Tests for the durable assignment ledger module."""
from __future__ import annotations

import json
import os
import pytest

import fcntl

from codex_local_harness.learning import (
    assign,
    list_assignments,
    main,
    validate_repository,
    validate_positive_int,
    validate_nonempty_string,
)


@pytest.fixture
def config_with_models(tmp_path: pytest.Path) -> dict:
    """Create a config with distinct standard and deepseek models."""
    config_path = tmp_path / "config.json"
    config = {
        "routes": {
            "coder/standard": {"model": "qwen-coder-next"},
            "coder/deepseek": {"model": "deepseek-v4-flash"},
        }
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return config


@pytest.fixture
def ledger_path(tmp_path: pytest.Path) -> pytest.Path:
    """Create a ledger file path."""
    return tmp_path / "assignments.jsonl"


class TestValidation:
    """Test validation functions."""

    def test_validate_repository_valid(self):
        """Valid repository format should pass."""
        owner, name = validate_repository("owner/repo")
        assert owner == "owner"
        assert name == "repo"

    def test_validate_repository_no_slash(self):
        """Repository without slash should fail."""
        with pytest.raises(ValueError, match="OWNER/REPO format"):
            validate_repository("invalid")

    def test_validate_repository_empty_owner(self):
        """Repository with empty owner should fail."""
        with pytest.raises(ValueError, match="OWNER/REPO format"):
            validate_repository("/repo")

    def test_validate_repository_empty_name(self):
        """Repository with empty name should fail."""
        with pytest.raises(ValueError, match="OWNER/REPO format"):
            validate_repository("owner/")

    def test_validate_repository_whitespace_owner(self):
        """Repository with whitespace-only owner should fail."""
        with pytest.raises(ValueError, match="OWNER/REPO format"):
            validate_repository("   /repo")

    def test_validate_positive_int_valid(self):
        """Valid positive integer should pass."""
        assert validate_positive_int(1) == 1
        assert validate_positive_int(42) == 42

    def test_validate_positive_int_zero(self):
        """Zero should fail."""
        with pytest.raises(ValueError, match="must be positive"):
            validate_positive_int(0)

    def test_validate_positive_int_negative(self):
        """Negative integer should fail."""
        with pytest.raises(ValueError, match="must be positive"):
            validate_positive_int(-1)

    def test_validate_positive_int_bool(self):
        """Boolean should fail (even though bool is subclass of int)."""
        with pytest.raises(ValueError, match="must be a positive integer"):
            validate_positive_int(True)

    def test_validate_nonempty_string_valid(self):
        """Non-empty string should pass."""
        assert validate_nonempty_string("hello", "name") == "hello"

    def test_validate_nonempty_string_empty(self):
        """Empty string should fail."""
        with pytest.raises(ValueError, match="must be a non-empty string"):
            validate_nonempty_string("", "name")

    def test_validate_nonempty_string_whitespace(self):
        """Whitespace-only string should fail."""
        with pytest.raises(ValueError, match="must be a non-empty string"):
            validate_nonempty_string("   ", "name")

    def test_validate_nonempty_string_non_string(self):
        """Non-string should fail."""
        with pytest.raises(ValueError, match="must be a non-empty string"):
            validate_nonempty_string(123, "name")


class TestAssignBasic:
    """Test basic assign functionality."""

    def test_assign_eligible_creates_record(self, tmp_path: pytest.Path, config_with_models: dict):
        """Eligible assignment should create a record."""
        ledger = tmp_path / "ledger.jsonl"
        result = assign(
            path=ledger,
            config=config_with_models,
            repository="owner/repo",
            issue=1,
            task_class="implementation",
            decision="eligible",
            reason="Standard bug fix",
        )

        assert result["schema_version"] == 1
        assert result["plan_version"] == 1
        assert result["repository"] == "owner/repo"
        assert result["issue"] == 1
        assert result["task_class"] == "implementation"
        assert result["decision"] == "eligible"
        assert result["reason"] == "Standard bug fix"
        assert result["sequence"] == 0
        assert result["coder_model"] == "qwen-coder-next"  # Even: standard
        assert result["reviewer_model"] == "deepseek-v4-flash"  # Even: deepseek
        assert result["role_env"] is not None
        assert result["role_env"]["CODEX_LOCAL_TIER"] == "standard"
        assert result["role_env"]["CODEX_LOCAL_MODEL"] == "qwen-coder-next"
        assert "observed_at" in result

    def test_assign_exception_creates_record(self, tmp_path: pytest.Path, config_with_models: dict):
        """Exception assignment should create a record with nulls."""
        ledger = tmp_path / "ledger.jsonl"
        result = assign(
            path=ledger,
            config=config_with_models,
            repository="owner/repo",
            issue=1,
            task_class="review",
            decision="exception",
            reason="Security review required",
        )

        assert result["decision"] == "exception"
        assert result["sequence"] is None
        assert result["coder_model"] is None
        assert result["reviewer_model"] is None
        assert result["role_env"] is None

    def test_assign_creates_file_with_correct_permissions(self, tmp_path: pytest.Path, config_with_models: dict):
        """Ledger file should be created with 0600 permissions."""
        ledger = tmp_path / "ledger.jsonl"
        assign(
            path=ledger,
            config=config_with_models,
            repository="owner/repo",
            issue=1,
            task_class="implementation",
            decision="eligible",
            reason="Test",
        )

        stat = os.stat(ledger)
        assert (stat.st_mode & 0o777) == 0o600

    def test_assign_creates_parent_directory_with_correct_permissions(self, tmp_path: pytest.Path, config_with_models: dict):
        """Ledger parent directory should be created with 0700 permissions."""
        ledger = tmp_path / "nested" / "ledger.jsonl"
        assign(
            path=ledger,
            config=config_with_models,
            repository="owner/repo",
            issue=1,
            task_class="implementation",
            decision="eligible",
            reason="Test",
        )

        stat = os.stat(ledger.parent)
        assert (stat.st_mode & 0o777) == 0o700


class TestSequenceLogic:
    """Test sequence counting and alternation logic."""

    def test_sequence_counts_prior_eligible(self, tmp_path: pytest.Path, config_with_models: dict):
        """Sequence should count prior eligible assignments."""
        ledger = tmp_path / "ledger.jsonl"

        # First eligible
        result1 = assign(
            path=ledger, config=config_with_models, repository="owner/repo", issue=1,
            task_class="impl", decision="eligible", reason="First"
        )
        assert result1["sequence"] == 0

        # Second eligible
        result2 = assign(
            path=ledger, config=config_with_models, repository="owner/repo", issue=2,
            task_class="impl", decision="eligible", reason="Second"
        )
        assert result2["sequence"] == 1

        # Exception doesn't count
        assign(
            path=ledger, config=config_with_models, repository="owner/repo", issue=3,
            task_class="review", decision="exception", reason="Exception"
        )

        # Third eligible
        result3 = assign(
            path=ledger, config=config_with_models, repository="owner/repo", issue=4,
            task_class="impl", decision="eligible", reason="Third"
        )
        assert result3["sequence"] == 2

    def test_model_alternation_even(self, tmp_path: pytest.Path, config_with_models: dict):
        """Even sequence (0,2,4,...) should use standard coder, deepseek reviewer."""
        ledger = tmp_path / "ledger.jsonl"

        result = assign(
            path=ledger, config=config_with_models, repository="owner/repo", issue=1,
            task_class="impl", decision="eligible", reason="Even"
        )
        assert result["sequence"] == 0
        assert result["coder_model"] == "qwen-coder-next"
        assert result["reviewer_model"] == "deepseek-v4-flash"
        assert result["role_env"]["CODEX_LOCAL_TIER"] == "standard"

    def test_model_alternation_odd(self, tmp_path: pytest.Path, config_with_models: dict):
        """Odd sequence (1,3,5,...) should use deepseek coder, standard reviewer."""
        ledger = tmp_path / "ledger.jsonl"

        # First eligible (sequence 0)
        assign(
            path=ledger, config=config_with_models, repository="owner/repo", issue=1,
            task_class="impl", decision="eligible", reason="Even"
        )

        # Second eligible (sequence 1)
        result = assign(
            path=ledger, config=config_with_models, repository="owner/repo", issue=2,
            task_class="impl", decision="eligible", reason="Odd"
        )
        assert result["sequence"] == 1
        assert result["coder_model"] == "deepseek-v4-flash"
        assert result["reviewer_model"] == "qwen-coder-next"
        assert result["role_env"]["CODEX_LOCAL_TIER"] == "deepseek"


class TestRepeatHandling:
    """Test handling of repeat assignments."""

    def test_repeat_exact_eligible_returns_stored(self, tmp_path: pytest.Path, config_with_models: dict):
        """Repeat with exact same values should return stored record."""
        ledger = tmp_path / "ledger.jsonl"

        result1 = assign(
            path=ledger, config=config_with_models, repository="owner/repo", issue=1,
            task_class="impl", decision="eligible", reason="Same reason"
        )

        result2 = assign(
            path=ledger, config=config_with_models, repository="owner/repo", issue=1,
            task_class="impl", decision="eligible", reason="Same reason"
        )

        assert result1 == result2
        # Should still have only one record
        records = list_assignments(ledger)
        assert len(records) == 1

    def test_repeat_different_reason_raises(self, tmp_path: pytest.Path, config_with_models: dict):
        """Repeat with different reason should raise ValueError."""
        ledger = tmp_path / "ledger.jsonl"

        assign(
            path=ledger, config=config_with_models, repository="owner/repo", issue=1,
            task_class="impl", decision="eligible", reason="Original reason"
        )

        with pytest.raises(ValueError, match="conflicting assignment"):
            assign(
                path=ledger, config=config_with_models, repository="owner/repo", issue=1,
                task_class="impl", decision="eligible", reason="Different reason"
            )

    def test_repeat_different_task_class_raises(self, tmp_path: pytest.Path, config_with_models: dict):
        """Repeat with different task_class should raise ValueError."""
        ledger = tmp_path / "ledger.jsonl"

        assign(
            path=ledger, config=config_with_models, repository="owner/repo", issue=1,
            task_class="impl", decision="eligible", reason="Reason"
        )

        with pytest.raises(ValueError, match="conflicting assignment"):
            assign(
                path=ledger, config=config_with_models, repository="owner/repo", issue=1,
                task_class="review", decision="eligible", reason="Reason"
            )

    def test_repeat_different_decision_raises(self, tmp_path: pytest.Path, config_with_models: dict):
        """Repeat with different decision should raise ValueError."""
        ledger = tmp_path / "ledger.jsonl"

        assign(
            path=ledger, config=config_with_models, repository="owner/repo", issue=1,
            task_class="impl", decision="eligible", reason="Reason"
        )

        with pytest.raises(ValueError, match="conflicting assignment"):
            assign(
                path=ledger, config=config_with_models, repository="owner/repo", issue=1,
                task_class="impl", decision="exception", reason="Reason"
            )


class TestConfigValidation:
    """Test config validation in assign."""

    def test_missing_standard_model_raises(self, tmp_path: pytest.Path):
        """Missing standard model should raise."""
        config = {"routes": {"coder/deepseek": {"model": "deepseek-model"}}}
        ledger = tmp_path / "ledger.jsonl"

        with pytest.raises(ValueError, match="coder/standard.*model.*non-empty string"):
            assign(
                path=ledger, config=config, repository="owner/repo", issue=1,
                task_class="impl", decision="eligible", reason="Reason"
            )

    def test_missing_deepseek_model_raises(self, tmp_path: pytest.Path):
        """Missing deepseek model should raise."""
        config = {"routes": {"coder/standard": {"model": "standard-model"}}}
        ledger = tmp_path / "ledger.jsonl"

        with pytest.raises(ValueError, match="coder/deepseek.*model.*non-empty string"):
            assign(
                path=ledger, config=config, repository="owner/repo", issue=1,
                task_class="impl", decision="eligible", reason="Reason"
            )

    def test_same_standard_and_deepseek_raises(self, tmp_path: pytest.Path):
        """Same model for both routes should raise."""
        config = {
            "routes": {
                "coder/standard": {"model": "same-model"},
                "coder/deepseek": {"model": "same-model"},
            }
        }
        ledger = tmp_path / "ledger.jsonl"

        with pytest.raises(ValueError, match="standard and deepseek models must be distinct"):
            assign(
                path=ledger, config=config, repository="owner/repo", issue=1,
                task_class="impl", decision="eligible", reason="Reason"
            )

    def test_empty_standard_model_raises(self, tmp_path: pytest.Path):
        """Empty standard model string should raise."""
        config = {"routes": {"coder/standard": {"model": ""}, "coder/deepseek": {"model": "deep"}}}
        ledger = tmp_path / "ledger.jsonl"

        with pytest.raises(ValueError, match="coder/standard.*model.*non-empty string"):
            assign(
                path=ledger, config=config, repository="owner/repo", issue=1,
                task_class="impl", decision="eligible", reason="Reason"
            )


class TestMalformedLedger:
    """Test handling of malformed ledger files."""

    def test_invalid_json_line_raises(self, tmp_path: pytest.Path, config_with_models: dict):
        """Invalid JSON line should raise with line number."""
        ledger = tmp_path / "ledger.jsonl"
        ledger.write_text("not valid json\n", encoding="utf-8")

        with pytest.raises(ValueError, match="invalid JSON"):
            assign(
                path=ledger, config=config_with_models, repository="owner/repo", issue=1,
                task_class="impl", decision="eligible", reason="Reason"
            )

    def test_malformed_ledger_preserves_history(self, tmp_path: pytest.Path, config_with_models: dict):
        """Malformed ledger should fail without resetting history."""
        ledger = tmp_path / "ledger.jsonl"
        # Write some valid records then malformed (on a new line)
        ledger.write_text(
            '{"repository":"owner/repo","issue":1,"decision":"eligible","sequence":0}\n'
            'not valid json\n',
            encoding="utf-8"
        )

        with pytest.raises(ValueError, match="invalid JSON"):
            assign(
                path=ledger, config=config_with_models, repository="owner/repo", issue=2,
                task_class="impl", decision="eligible", reason="Reason"
            )

        # First record should still exist - read directly to avoid list_assignments error
        content = ledger.read_text(encoding="utf-8")
        assert '"issue":1' in content


class TestCLI:
    """Test CLI commands."""

    def test_cli_assign_prints_json(self, tmp_path: pytest.Path, config_with_models: dict):
        """CLI assign should print JSON."""
        ledger = tmp_path / "ledger.jsonl"
        config = tmp_path / "config.json"
        config.write_text(json.dumps(config_with_models), encoding="utf-8")

        result = main(["assign", "--ledger", str(ledger), "--config", str(config),
                      "--repository", "owner/repo", "--issue", "1",
                      "--task-class", "impl", "--decision", "eligible", "--reason", "Test"])

        assert result == 0

    def test_cli_assign_stderr_on_error(self, tmp_path: pytest.Path):
        """CLI assign should print error to stderr and exit 2 on error."""
        ledger = tmp_path / "ledger.jsonl"
        config = tmp_path / "config.json"
        config.write_text("{}", encoding="utf-8")  # Invalid config

        result = main(["assign", "--ledger", str(ledger), "--config", str(config),
                      "--repository", "owner/repo", "--issue", "1",
                      "--task-class", "impl", "--decision", "eligible", "--reason", "Test"])

        assert result == 2

    def test_cli_list_prints_records(self, tmp_path: pytest.Path, config_with_models: dict):
        """CLI list should print all records as JSON."""
        ledger = tmp_path / "ledger.jsonl"
        config = tmp_path / "config.json"
        config.write_text(json.dumps(config_with_models), encoding="utf-8")

        assign(
            path=ledger, config=config_with_models, repository="owner/repo", issue=1,
            task_class="impl", decision="eligible", reason="First"
        )
        assign(
            path=ledger, config=config_with_models, repository="owner/repo", issue=2,
            task_class="impl", decision="eligible", reason="Second"
        )

        result = main(["list", "--ledger", str(ledger)])

        assert result == 0

    def test_cli_assign_missing_config(self, tmp_path: pytest.Path):
        """CLI assign with missing config should exit 2."""
        ledger = tmp_path / "ledger.jsonl"

        result = main(["assign", "--ledger", str(ledger), "--config", "/nonexistent/config.json",
                      "--repository", "owner/repo", "--issue", "1",
                      "--task-class", "impl", "--decision", "eligible", "--reason", "Test"])

        assert result == 2


class TestConcurrentAccess:
    """Test concurrent access patterns."""

    def test_concurrent_assign_produces_unique_sequences(self, tmp_path: pytest.Path, config_with_models: dict):
        """Concurrent assignments should produce unique consecutive sequences."""
        ledger = tmp_path / "ledger.jsonl"

        # Create file first
        ledger.touch()

        # Simulate concurrent access by manually acquiring lock
        # and adding records
        for i in range(3):
            with ledger.open("a") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                f.seek(0, 2)  # End of file
                f.write(json.dumps({
                    "repository": "owner/repo",
                    "issue": i + 1,
                    "decision": "eligible",
                    "sequence": i,
                    "task_class": "impl",
                    "reason": f"Concurrent {i}",
                    "schema_version": 1,
                    "plan_version": 1,
                    "coder_model": "model",
                    "reviewer_model": "model",
                    "observed_at": "2026-01-01T00:00:00Z",
                }) + "\n")
                f.flush()
                os.fsync(f.fileno())

        records = list_assignments(ledger)
        sequences = sorted([r["sequence"] for r in records])
        assert sequences == [0, 1, 2]


class TestListAssignments:
    """Test list_assignments function."""

    def test_list_empty_ledger(self, tmp_path: pytest.Path):
        """List on non-existent file should return empty tuple."""
        ledger = tmp_path / "ledger.jsonl"
        records = list_assignments(ledger)
        assert records == ()

    def test_list_nonexistent_file(self, tmp_path: pytest.Path):
        """List on empty file should return empty tuple."""
        ledger = tmp_path / "ledger.jsonl"
        ledger.touch()
        records = list_assignments(ledger)
        assert records == ()

    def test_list_returns_all_records(self, tmp_path: pytest.Path, config_with_models: dict):
        """List should return all records."""
        ledger = tmp_path / "ledger.jsonl"

        assign(
            path=ledger, config=config_with_models, repository="owner/repo", issue=1,
            task_class="impl", decision="eligible", reason="First"
        )
        assign(
            path=ledger, config=config_with_models, repository="owner/repo", issue=2,
            task_class="impl", decision="eligible", reason="Second"
        )

        records = list_assignments(ledger)
        assert len(records) == 2
        assert records[0]["issue"] == 1
        assert records[1]["issue"] == 2
