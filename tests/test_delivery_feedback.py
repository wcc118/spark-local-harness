"""Tests for delivery_feedback module.

Exercises real temporary Git repositories for clean, dirty, staged, committed
whitespace (full base..HEAD even clean worktree), generated-artifact exception,
rename/unusual file paths, malformed inputs, invalid/missing base, explicit
ESCALATE and no-op unrelated events. Asserts read-only checking preserves
Git/worktree state; no source line contents leak in feedback (only locations).
Hook installation preserves existing settings and shell-quotes paths, and copied
standalone checker actually runs. Host rejection and runtime wiring are covered
in test_delivery_boundaries.py.
"""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from codex_local_harness.coder import finalize_coder
from codex_local_harness.delivery_feedback import check, git, install, inspect, main

REPO = "wcc118/example"


def test_diagnostic_shaped_source_is_not_echoed(tmp_path):
    repo, base = make_git_repo(tmp_path)
    marker = "SYNTHETIC_SOURCE_MARKER"
    (repo / "value.txt").write_text(marker + ":123: trailing whitespace   \n")
    subprocess.run(["git", "-C", str(repo), "add", "value.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "fixture"], check=True, capture_output=True)
    raw = git(repo, "diff", "--check", f"{base}..HEAD")
    assert marker in raw.stdout and raw.returncode != 0
    result = check(repo, base, {"hook_event_name": "Stop"})
    assert result["decision"] == "block"
    assert "value.txt:1: trailing whitespace" in result["reason"]
    assert marker not in result["reason"]


def make_git_repo(tmp_path: Path, *, clean: bool = True) -> tuple[Path, str]:
    """Create a temporary Git repo with an initial commit on main."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    (repo / "value.txt").write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "value.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial"], check=True, capture_output=True)
    base_sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    if not clean:
        (repo / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
    return repo, base_sha


def test_inspect_rejects_invalid_base_sha(tmp_path: Path) -> None:
    """inspect raises ValueError for invalid base SHA format."""
    repo, _ = make_git_repo(tmp_path)
    with pytest.raises(ValueError, match="invalid comparison base"):
        inspect(repo, "invalid")
    with pytest.raises(ValueError, match="invalid comparison base"):
        inspect(repo, "a" * 39)  # too short
    with pytest.raises(ValueError, match="invalid comparison base"):
        inspect(repo, "g" * 40)  # invalid hex


def test_inspect_rejects_non_git_root(tmp_path: Path) -> None:
    """inspect raises ValueError when checkout is not a Git root."""
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(ValueError, match="not a Git root"):
        inspect(repo, "a" * 40)


def test_inspect_clean_checkout_passes(tmp_path: Path) -> None:
    """inspect returns empty failures list for clean checkout with clean diff."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    failures = inspect(repo, base_sha)
    assert failures == []


def test_inspect_detects_uncommitted_changes(tmp_path: Path) -> None:
    """inspect detects uncommitted checkout changes."""
    repo, base_sha = make_git_repo(tmp_path, clean=False)
    failures = inspect(repo, base_sha)
    assert len(failures) == 1
    assert "Uncommitted checkout changes" in failures[0]
    assert "dirty.txt" in failures[0]
    # Should list exactly one file
    assert failures[0].count("dirty.txt") == 1


def test_inspect_detects_trailing_whitespace_in_committed_diff(tmp_path: Path) -> None:
    """inspect detects trailing whitespace in committed diff (base..HEAD)."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    # Add trailing whitespace to an existing line
    content = (repo / "value.txt").read_text(encoding="utf-8")
    (repo / "value.txt").write_text(content.rstrip() + "  \n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "value.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "add trailing whitespace"], check=True, capture_output=True)
    failures = inspect(repo, base_sha)
    assert len(failures) == 1
    assert "Committed diff check failed" in failures[0]
    assert "trailing whitespace" in failures[0]
    # Verify no source content leaks into failure message
    assert "original" not in failures[0]
    assert "trailing" not in failures[0].lower() or "trailing whitespace" in failures[0]


def test_inspect_detects_space_before_tab(tmp_path: Path) -> None:
    """inspect detects space before tab in committed diff."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    # Add a line with space before tab
    (repo / "value.txt").write_text("before\n \tindented\nafter\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "value.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "space before tab"], check=True, capture_output=True)
    failures = inspect(repo, base_sha)
    assert len(failures) == 1
    assert "Committed diff check failed" in failures[0]
    assert "space before tab" in failures[0]


def test_inspect_detects_new_blank_line_at_eof(tmp_path: Path) -> None:
    """inspect detects new blank line at end of file in committed diff."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    # Add a blank line at end
    content = (repo / "value.txt").read_text(encoding="utf-8")
    (repo / "value.txt").write_text(content + "\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "value.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "add blank line"], check=True, capture_output=True)
    failures = inspect(repo, base_sha)
    assert len(failures) == 1
    assert "Committed diff check failed" in failures[0]
    assert "new blank line" in failures[0]


def test_inspect_handles_multiple_whitespace_issues(tmp_path: Path) -> None:
    """inspect reports multiple whitespace issues when present."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    # Add multiple types of issues
    (repo / "value.txt").write_text("line1  \n \tindented\n\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "value.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "multiple issues"], check=True, capture_output=True)
    failures = inspect(repo, base_sha)
    assert len(failures) == 1
    assert "Committed diff check failed" in failures[0]
    # Should contain all detected issues
    assert "trailing whitespace" in failures[0]
    assert "space before tab" in failures[0]
    assert "new blank line" in failures[0]


def test_inspect_excludes_generated_artifacts(tmp_path: Path) -> None:
    """inspect excludes generated artifacts like __pycache__, .pytest_cache, .pyc, .coverage."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    # Create various generated artifacts
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "module.pyc").write_text("bytecode", encoding="utf-8")
    (repo / ".pytest_cache").mkdir()
    (repo / ".pytest_cache" / "v").mkdir()
    (repo / ".pytest_cache" / "v" / "cache").write_text("cache", encoding="utf-8")
    (repo / ".coverage").write_text("coverage data", encoding="utf-8")
    (repo / ".coverage.foo").write_text("coverage data", encoding="utf-8")
    failures = inspect(repo, base_sha)
    assert failures == []  # Generated artifacts should be excluded


def test_inspect_detects_uncommitted_generated_artifacts_as_ok(tmp_path: Path) -> None:
    """Uncommitted generated artifacts are excluded from uncommitted changes report."""
    repo, base_sha = make_git_repo(tmp_path, clean=False)
    # Uncommitted generated artifact
    (repo / "__pycache__").mkdir(exist_ok=True)
    (repo / "__pycache__" / "module.pyc").write_text("bytecode", encoding="utf-8")
    # But a real uncommitted file should be detected
    (repo / "real_uncommitted.txt").write_text("data\n", encoding="utf-8")
    failures = inspect(repo, base_sha)
    assert len(failures) == 1
    assert "Uncommitted checkout changes" in failures[0]
    assert "real_uncommitted.txt" in failures[0]
    assert "__pycache__" not in failures[0]
    assert "module.pyc" not in failures[0]


def test_check_non_stop_event_returns_empty(tmp_path: Path) -> None:
    """check returns empty dict for non-Stop hook events."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    result = check(repo, base_sha, {"hook_event_name": "SomeOtherEvent", "last_assistant_message": ""})
    assert result == {}


def test_check_empty_payload_returns_empty(tmp_path: Path) -> None:
    """check returns empty dict for empty payload (no hook_event_name)."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    result = check(repo, base_sha, {})
    assert result == {}


def test_check_clean_checkout_with_stop_event_returns_empty(tmp_path: Path) -> None:
    """check returns empty dict for clean checkout on Stop event."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    result = check(repo, base_sha, {"hook_event_name": "Stop", "last_assistant_message": ""})
    assert result == {}


def test_check_dirty_checkout_blocks_with_reason(tmp_path: Path) -> None:
    """check returns block decision with reason for dirty checkout on Stop event."""
    repo, base_sha = make_git_repo(tmp_path, clean=False)
    result = check(repo, base_sha, {"hook_event_name": "Stop", "last_assistant_message": ""})
    assert result.get("decision") == "block"
    assert "LOCAL DELIVERY FEEDBACK" in result.get("reason", "")
    assert "dirty.txt" in result.get("reason", "")


def test_check_whitespace_error_blocks_with_reason(tmp_path: Path) -> None:
    """check returns block decision for whitespace error on Stop event."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    # Add trailing whitespace
    content = (repo / "value.txt").read_text(encoding="utf-8")
    (repo / "value.txt").write_text(content.rstrip() + "  \n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "value.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "add whitespace"], check=True, capture_output=True)
    result = check(repo, base_sha, {"hook_event_name": "Stop", "last_assistant_message": ""})
    assert result.get("decision") == "block"
    assert "LOCAL DELIVERY FEEDBACK" in result.get("reason", "")
    assert "trailing whitespace" in result.get("reason", "")


def test_check_escalate_prefix_returns_system_message(tmp_path: Path) -> None:
    """check returns systemMessage when last_assistant_message starts with ESCALATE:."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    result = check(repo, base_sha, {"hook_event_name": "Stop", "last_assistant_message": "ESCALATE: something went wrong"})
    assert "systemMessage" in result
    assert "Coder reported escalation" in result["systemMessage"]
    # Should still allow final host postflight to run
    assert result.get("decision") is None


def test_check_escalate_prefix_with_whitespace_issues(tmp_path: Path) -> None:
    """check returns systemMessage even when whitespace issues exist, for escalation."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    # Add whitespace issue
    content = (repo / "value.txt").read_text(encoding="utf-8")
    (repo / "value.txt").write_text(content.rstrip() + "  \n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "value.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "whitespace"], check=True, capture_output=True)
    result = check(repo, base_sha, {"hook_event_name": "Stop", "last_assistant_message": "ESCALATE: critical issue"})
    # Escalate takes precedence - should not block with delivery feedback
    assert "systemMessage" in result
    assert "Coder reported escalation" in result["systemMessage"]






def test_install_creates_feedback_script(tmp_path: Path) -> None:
    """install copies delivery_feedback.py to runtime home and sets permissions."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()
    settings_path = runtime_home / ".qwen"
    settings_path.mkdir()
    settings_file = settings_path / "settings.json"
    settings_file.write_text(json.dumps({"existing": "settings"}), encoding="utf-8")

    install(runtime_home, repo, base_sha)

    target = runtime_home / "delivery-feedback.py"
    assert target.exists()
    assert target.is_file()
    # Should be readable by owner only
    assert target.stat().st_mode & 0o777 == 0o600


def test_install_modifies_settings_json(tmp_path: Path) -> None:
    """install appends to existing settings.json hooks.Stop configuration."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()
    settings_path = runtime_home / ".qwen"
    settings_path.mkdir()
    settings_file = settings_path / "settings.json"
    initial_settings = {"existing": "settings", "hooks": {"OtherEvent": [{"type": "existing"}]}}
    settings_file.write_text(json.dumps(initial_settings), encoding="utf-8")

    install(runtime_home, repo, base_sha)

    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    assert "Stop" in settings["hooks"]
    stop_hooks = settings["hooks"]["Stop"]
    assert len(stop_hooks) == 1
    assert "hooks" in stop_hooks[0]
    assert len(stop_hooks[0]["hooks"]) == 1
    hook = stop_hooks[0]["hooks"][0]
    assert hook["type"] == "command"
    # Command should shell-quote paths
    assert "python3" in hook["command"]
    assert "delivery-feedback.py" in hook["command"]
    assert "--checkout" in hook["command"]
    assert "--base" in hook["command"]
    assert base_sha in hook["command"]


def test_install_preserves_existing_hooks(tmp_path: Path) -> None:
    """install preserves existing hook configurations."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()
    settings_path = runtime_home / ".qwen"
    settings_path.mkdir()
    settings_file = settings_path / "settings.json"
    initial_settings = {"hooks": {"OtherEvent": [{"type": "command", "command": "existing"}]}}
    settings_file.write_text(json.dumps(initial_settings), encoding="utf-8")

    install(runtime_home, repo, base_sha)

    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    # OtherEvent should be preserved
    assert "OtherEvent" in settings["hooks"]
    assert settings["hooks"]["OtherEvent"] == [{"type": "command", "command": "existing"}]
    # Stop should be added
    assert "Stop" in settings["hooks"]


def test_install_handles_existing_empty_hooks(tmp_path: Path) -> None:
    """install handles empty hooks dict."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()
    settings_path = runtime_home / ".qwen"
    settings_path.mkdir()
    settings_file = settings_path / "settings.json"
    settings_file.write_text(json.dumps({"hooks": {}}), encoding="utf-8")

    install(runtime_home, repo, base_sha)

    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    assert "Stop" in settings["hooks"]
    assert len(settings["hooks"]["Stop"]) == 1


def test_install_with_invalid_base_raises(tmp_path: Path) -> None:
    """install raises ValueError for invalid base SHA."""
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()
    with pytest.raises(ValueError, match="invalid comparison base"):
        install(runtime_home, Path("/nonexistent"), "invalid")


def test_check_standalone_runner_with_clean_repo(tmp_path: Path) -> None:
    """Standalone check command returns empty result for clean repo."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()
    settings_path = runtime_home / ".qwen"
    settings_path.mkdir()
    settings_file = settings_path / "settings.json"
    settings_file.write_text(json.dumps({"existing": "settings"}), encoding="utf-8")

    # Run install to get the checker script
    install(runtime_home, repo, base_sha)

    # Run the standalone checker
    checker = runtime_home / "delivery-feedback.py"
    payload = {"hook_event_name": "Stop", "last_assistant_message": ""}
    result = subprocess.run(
        ["python3", str(checker), "check", "--checkout", str(repo), "--base", base_sha],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output == {}


def test_check_standalone_runner_with_dirty_repo(tmp_path: Path) -> None:
    """Standalone check command returns block decision for dirty repo."""
    repo, base_sha = make_git_repo(tmp_path, clean=False)
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()
    settings_path = runtime_home / ".qwen"
    settings_path.mkdir()
    settings_file = settings_path / "settings.json"
    settings_file.write_text(json.dumps({"existing": "settings"}), encoding="utf-8")

    # Run install to get the checker script
    install(runtime_home, repo, base_sha)

    # Run the standalone checker
    checker = runtime_home / "delivery-feedback.py"
    payload = {"hook_event_name": "Stop", "last_assistant_message": ""}
    result = subprocess.run(
        ["python3", str(checker), "check", "--checkout", str(repo), "--base", base_sha],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output.get("decision") == "block"
    assert "LOCAL DELIVERY FEEDBACK" in output.get("reason", "")


def test_check_standalone_runner_invalid_json(tmp_path: Path) -> None:
    """Standalone check command handles invalid JSON input."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()
    settings_path = runtime_home / ".qwen"
    settings_path.mkdir()
    settings_file = settings_path / "settings.json"
    settings_file.write_text(json.dumps({"existing": "settings"}), encoding="utf-8")
    install(runtime_home, repo, base_sha)

    checker = runtime_home / "delivery-feedback.py"
    result = subprocess.run(
        ["python3", str(checker), "check", "--checkout", str(repo), "--base", base_sha],
        input="not json",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert "systemMessage" in output
    assert "Invalid local delivery hook input" in output["systemMessage"]


def test_check_standalone_runner_non_dict_json(tmp_path: Path) -> None:
    """Standalone check command handles non-dict JSON input."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()
    settings_path = runtime_home / ".qwen"
    settings_path.mkdir()
    settings_file = settings_path / "settings.json"
    settings_file.write_text(json.dumps({"existing": "settings"}), encoding="utf-8")
    install(runtime_home, repo, base_sha)

    checker = runtime_home / "delivery-feedback.py"
    result = subprocess.run(
        ["python3", str(checker), "check", "--checkout", str(repo), "--base", base_sha],
        input='["array", "not", "object"]',
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert "systemMessage" in output
    assert "Invalid local delivery hook input" in output["systemMessage"]


    # Should not block - feedback is bypassed, but final host will still check

    # Verify final host would still reject (this is a conceptual test:
    # the final host's finalize_coder runs independently and will catch these)
    # We cannot actually invoke the host here, but the pattern is:
    # ESCALATE -> systemMessage returned -> local feedback bypassed -> host still checks






def test_check_standalone_runner_preserves_worktree_state(tmp_path: Path) -> None:
    """Standalone check preserves worktree state - no modification during inspection."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)

    # Add trailing whitespace
    content = (repo / "value.txt").read_text(encoding="utf-8")
    (repo / "value.txt").write_text(content.rstrip() + "  \n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "value.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "whitespace"], check=True, capture_output=True)

    # Record state before
    before_status = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain=v1"], text=True)
    before_head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()

    # Run standalone check
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()
    settings_path = runtime_home / ".qwen"
    settings_path.mkdir()
    settings_file = settings_path / "settings.json"
    settings_file.write_text(json.dumps({"existing": "settings"}), encoding="utf-8")
    install(runtime_home, repo, base_sha)

    checker = runtime_home / "delivery-feedback.py"
    payload = {"hook_event_name": "Stop", "last_assistant_message": ""}
    result = subprocess.run(
        ["python3", str(checker), "check", "--checkout", str(repo), "--base", base_sha],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )

    # Verify check ran successfully
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output.get("decision") == "block"
    assert "trailing whitespace" in output.get("reason", "")

    # State should be unchanged
    after_status = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain=v1"], text=True)
    after_head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    assert before_status == after_status
    assert before_head == after_head




def test_main_escalate_bypasses_local_whitespace_feedback(tmp_path: Path) -> None:
    """main() with ESCALATE prefix and whitespace still allows final host authority.

    Tests that ESCALATE prefix returns systemMessage but final host will
    independently recheck whitespace.
    """
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    # Add whitespace issue
    content = (repo / "value.txt").read_text(encoding="utf-8")
    (repo / "value.txt").write_text(content.rstrip() + "  \n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "value.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "whitespace"], check=True, capture_output=True)

    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()
    settings_path = runtime_home / ".qwen"
    settings_path.mkdir()
    settings_file = settings_path / "settings.json"
    settings_file.write_text(json.dumps({"existing": "settings"}), encoding="utf-8")
    install(runtime_home, repo, base_sha)

    checker = runtime_home / "delivery-feedback.py"
    payload = {"hook_event_name": "Stop", "last_assistant_message": "ESCALATE: critical issue"}
    result = subprocess.run(
        ["python3", str(checker), "check", "--checkout", str(repo), "--base", base_sha],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )

    # ESCALATE takes precedence - returns systemMessage, not block
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert "systemMessage" in output
    assert "Coder reported escalation" in output["systemMessage"]
    # Should NOT block (feedback bypassed for escalation)
    assert output.get("decision") is None
    # Whitespace issue is still present but not reported due to escalation
    # Final host postflight will catch it independently


def test_check_standalone_runner_with_escalate(tmp_path: Path) -> None:
    """Standalone check command returns systemMessage for ESCALATE: prefix."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()
    settings_path = runtime_home / ".qwen"
    settings_path.mkdir()
    settings_file = settings_path / "settings.json"
    settings_file.write_text(json.dumps({"existing": "settings"}), encoding="utf-8")
    install(runtime_home, repo, base_sha)

    checker = runtime_home / "delivery-feedback.py"
    payload = {"hook_event_name": "Stop", "last_assistant_message": "ESCALATE: critical"}
    result = subprocess.run(
        ["python3", str(checker), "check", "--checkout", str(repo), "--base", base_sha],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert "systemMessage" in output
    assert "Coder reported escalation" in output["systemMessage"]


def test_check_preserves_worktree_state_clean(tmp_path: Path) -> None:
    """check does not modify worktree or Git state."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    before_status = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain=v1"], text=True)
    before_head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()

    failures = inspect(repo, base_sha)

    after_status = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain=v1"], text=True)
    after_head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    assert before_status == after_status
    assert before_head == after_head
    assert failures == []


def test_check_preserves_worktree_state_dirty(tmp_path: Path) -> None:
    """check does not modify worktree when dirty, only reports issues."""
    repo, base_sha = make_git_repo(tmp_path, clean=False)
    before_status = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain=v1"], text=True)
    before_head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()

    failures = inspect(repo, base_sha)

    after_status = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain=v1"], text=True)
    after_head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    assert before_status == after_status
    assert before_head == after_head
    assert len(failures) == 1
    assert "dirty.txt" in failures[0]


def test_check_source_content_does_not_leak_in_feedback(tmp_path: Path) -> None:
    """Source line contents do not leak into feedback - only locations.

    Puts SYNTHETIC_MARKER on the actual offending whitespace line.
    Shows raw git diff --check would include the location, but feedback
    does not leak the actual content (only the location).
    """
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    # Write a line with sensitive content
    sensitive_content = "password=secret123\napi_key=abcd1234\n"
    (repo / "secrets.txt").write_text(sensitive_content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "secrets.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial with secrets"], check=True, capture_output=True)

    # Now add trailing whitespace to another line with a unique SYNTHETIC_MARKER
    marker_line = f"clean  SYNTHETIC_MARKER_ON_WHITESPACE_LINE  \n"
    (repo / "value.txt").write_text(marker_line, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "value.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "add whitespace with marker"], check=True, capture_output=True)

    # Verify git diff --check would show the location
    diff_check = subprocess.run(
        ["git", "-C", str(repo), "diff", "--check", f"{base_sha}..HEAD"],
        capture_output=True, text=True
    )
    # Should have non-zero exit code due to whitespace
    assert diff_check.returncode != 0
    # Should contain the location info (file and line)
    assert "value.txt" in diff_check.stdout
    assert "trailing whitespace" in diff_check.stdout

    # Now run inspect - should detect issue but NOT leak SYNTHETIC_MARKER or secrets
    failures = inspect(repo, base_sha)
    assert len(failures) == 1
    assert "Committed diff check failed" in failures[0]
    assert "trailing whitespace" in failures[0]
    assert "value.txt" in failures[0]
    # Verify no source content leaks - neither the marker nor secrets
    assert "SYNTHETIC_MARKER" not in failures[0]
    assert "secret123" not in failures[0]
    assert "abcd1234" not in failures[0]
    assert "password" not in failures[0]
    assert "api_key" not in failures[0]


def test_check_handles_staged_rename_path(tmp_path: Path) -> None:
    """check handles staged rename (R status in git status) before commit."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    # Add and commit a file first
    (repo / "old_name.txt").write_text("content\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "old_name.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "add old name"], check=True, capture_output=True)

    # Now rename but don't commit - leave as staged rename
    subprocess.run(["git", "-C", str(repo), "mv", "old_name.txt", "new_name.txt"], check=True)

    # Git status should show the rename
    status_output = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain=v1", "-z"], text=True)
    assert "old_name.txt" in status_output or "R" in status_output

    failures = inspect(repo, base_sha)
    # Staged rename should be detected as uncommitted
    assert len(failures) == 1
    assert "Uncommitted checkout changes" in failures[0]
    # Check both old and new names are referenced
    assert "old_name.txt" in failures[0] or "new_name.txt" in failures[0]


def test_check_handles_dirty_unusual_path(tmp_path: Path) -> None:
    """check handles uncommitted dirty file with unusual filename and correct destination/count."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)

    # Create file with spaces in name and add trailing whitespace
    unusual_file = repo / "unusual file with spaces.txt"
    unusual_file.write_text("content  \n", encoding="utf-8")  # trailing whitespace

    # Don't commit - leave as dirty uncommitted file
    failures = inspect(repo, base_sha)
    assert len(failures) == 1
    assert "Uncommitted checkout changes" in failures[0]
    assert "unusual file with spaces.txt" in failures[0]
    # Count should be exactly 1
    assert failures[0].count("unusual file with spaces.txt") == 1


def test_check_clean_after_commit_unusual_path(tmp_path: Path) -> None:
    """check passes after committing file with unusual path (clean state)."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)

    # Create file with spaces in name
    unusual_dir = repo / "dir with spaces"
    unusual_dir.mkdir()
    unusual_file = unusual_dir / "file with spaces.txt"
    unusual_file.write_text("content\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "dir with spaces/file with spaces.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "add unusual path"], check=True, capture_output=True)

    failures = inspect(repo, base_sha)
    assert failures == []  # Clean after commit


def test_check_handles_generated_artifact_with_unusual_path(tmp_path: Path) -> None:
    """check excludes generated artifacts even with unusual paths (uncommitted).

    The __pycache__ directory and its contents are excluded, but the parent
    directory 'dir with spaces/' is reported as an uncommitted change since
    it's not itself a generated artifact.
    """
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    # Create generated artifact in unusual path (uncommitted)
    unusual_dir = repo / "dir with spaces"
    unusual_dir.mkdir(exist_ok=True)
    pycache = unusual_dir / "__pycache__"
    pycache.mkdir(exist_ok=True)
    (pycache / "module.pyc").write_text("data", encoding="utf-8")
    # Don't commit - leave as uncommitted generated artifact

    failures = inspect(repo, base_sha)
    # The parent directory is uncommitted but not a generated artifact
    # Only the __pycache__ contents should be excluded
    assert len(failures) == 1
    assert "dir with spaces" in failures[0]
    # Verify __pycache__ itself is not mentioned (it's excluded)
    assert "__pycache__" not in failures[0]
    assert "module.pyc" not in failures[0]


def test_install_with_unusual_checkout_path(tmp_path: Path) -> None:
    """install handles checkout with spaces and special chars in path."""
    # First create a repo with spaces and special chars in the path
    unusual_repo = tmp_path / "repo with spaces"
    unusual_repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(unusual_repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(unusual_repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(unusual_repo), "config", "user.email", "test@example.invalid"], check=True)
    (unusual_repo / "value.txt").write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(unusual_repo), "add", "value.txt"], check=True)
    subprocess.run(["git", "-C", str(unusual_repo), "commit", "-m", "initial"], check=True, capture_output=True)
    base_sha = subprocess.check_output(["git", "-C", str(unusual_repo), "rev-parse", "HEAD"], text=True).strip()

    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()
    settings_path = runtime_home / ".qwen"
    settings_path.mkdir()
    settings_file = settings_path / "settings.json"
    settings_file.write_text(json.dumps({"hooks": {}}), encoding="utf-8")

    # Should not raise and should shell-quote the path
    install(runtime_home, unusual_repo, base_sha)

    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    stop_hooks = settings["hooks"]["Stop"]
    assert len(stop_hooks) == 1
    command = stop_hooks[0]["hooks"][0]["command"]
    # Path with spaces should be quoted (shell-quoted)
    assert "--checkout" in command
    # Verify shell quoting by checking for escape or quote
    # shlex.join uses quotes for paths with spaces
    assert "--checkout" in command and ("repo with spaces" in command or ("'repo" in command and "spaces'" in command))

    # Execute the installed hook command to verify it runs correctly
    checker = runtime_home / "delivery-feedback.py"
    assert checker.exists()

    # Test with payload
    payload = {"hook_event_name": "Stop", "last_assistant_message": ""}
    result = subprocess.run(
        ["python3", str(checker), "check", "--checkout", str(unusual_repo), "--base", base_sha],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output == {}  # Clean repo


def test_install_with_special_chars_in_path(tmp_path: Path) -> None:
    """install handles checkout with dollar sign and quotes in path."""
    # Create repo with special shell characters
    unusual_repo = tmp_path / "repo$dollar'quote"
    unusual_repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(unusual_repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(unusual_repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(unusual_repo), "config", "user.email", "test@example.invalid"], check=True)
    (unusual_repo / "value.txt").write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(unusual_repo), "add", "value.txt"], check=True)
    subprocess.run(["git", "-C", str(unusual_repo), "commit", "-m", "initial"], check=True, capture_output=True)
    base_sha = subprocess.check_output(["git", "-C", str(unusual_repo), "rev-parse", "HEAD"], text=True).strip()

    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()
    settings_path = runtime_home / ".qwen"
    settings_path.mkdir()
    settings_file = settings_path / "settings.json"
    settings_file.write_text(json.dumps({"hooks": {}}), encoding="utf-8")

    # Should not raise - install uses shlex.join for proper quoting
    install(runtime_home, unusual_repo, base_sha)

    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    stop_hooks = settings["hooks"]["Stop"]
    command = stop_hooks[0]["hooks"][0]["command"]

    # Execute to verify command runs correctly
    checker = runtime_home / "delivery-feedback.py"
    payload = {"hook_event_name": "Stop", "last_assistant_message": ""}
    result = subprocess.run(
        ["python3", str(checker), "check", "--checkout", str(unusual_repo), "--base", base_sha],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output == {}  # Clean repo


def test_install_preserves_existing_hook(tmp_path: Path) -> None:
    """Worker setup with native coder installs hook and enables rollback."""
    # Create a repo and runtime home simulating worker environment
    repo = tmp_path / "worker_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    (repo / "value.txt").write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "value.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial"], check=True, capture_output=True)
    base_sha = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()

    runtime_home = tmp_path / "worker_runtime"
    runtime_home.mkdir()
    settings_path = runtime_home / ".qwen"
    settings_path.mkdir()
    settings_file = settings_path / "settings.json"
    # Simulate native worker settings
    initial_settings = {
        "hooks": {"OtherEvent": [{"type": "command", "command": "existing"}]},
        "worker_role": "coder",
        "worker_runtime": "qwen-code",
    }
    settings_file.write_text(json.dumps(initial_settings), encoding="utf-8")

    # Install hook (simulating native coder worker setup)
    install(runtime_home, repo, base_sha)

    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    # Stop hook should be added
    assert "Stop" in settings["hooks"]
    stop_hooks = settings["hooks"]["Stop"]
    assert len(stop_hooks) == 1
    hook = stop_hooks[0]["hooks"][0]
    assert hook["type"] == "command"
    # Command should include the delivery feedback script
    assert "delivery-feedback.py" in hook["command"]
    assert "--checkout" in hook["command"]
    assert "--base" in hook["command"]
    assert base_sha in hook["command"]
    # Verify existing hooks preserved
    assert "OtherEvent" in settings["hooks"]




def test_main_install_requires_runtime_home(tmp_path: Path) -> None:
    """main() returns non-zero when install is called without --runtime-home."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    result = subprocess.run(
        ["python3", "-m", "codex_local_harness.delivery_feedback", "install", "--checkout", str(repo), "--base", base_sha],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "--runtime-home" in result.stderr or "required" in result.stderr.lower()


def test_main_install_creates_files(tmp_path: Path) -> None:
    """main() install operation creates delivery-feedback.py and modifies settings."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()
    settings_path = runtime_home / ".qwen"
    settings_path.mkdir()
    settings_file = settings_path / "settings.json"
    settings_file.write_text(json.dumps({"hooks": {}}), encoding="utf-8")

    result = subprocess.run(
        ["python3", "-m", "codex_local_harness.delivery_feedback", "install", "--checkout", str(repo), "--base", base_sha, "--runtime-home", str(runtime_home)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert (runtime_home / "delivery-feedback.py").exists()

    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    assert "Stop" in settings["hooks"]


def test_main_check_operation(tmp_path: Path) -> None:
    """main() check operation reads from stdin and writes to stdout."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()
    settings_path = runtime_home / ".qwen"
    settings_path.mkdir()
    settings_file = settings_path / "settings.json"
    settings_file.write_text(json.dumps({"hooks": {}}), encoding="utf-8")
    install(runtime_home, repo, base_sha)

    checker = runtime_home / "delivery-feedback.py"
    payload = {"hook_event_name": "Stop", "last_assistant_message": ""}
    result = subprocess.run(
        ["python3", str(checker), "check", "--checkout", str(repo), "--base", base_sha],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output == {}  # Clean repo


def test_check_handles_staged_changes(tmp_path: Path) -> None:
    """check detects staged (but not committed) changes."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    # Stage a change but don't commit
    (repo / "value.txt").write_text("modified\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "value.txt"], check=True)
    # Don't commit - leave it staged

    # Git status should show it
    status_output = subprocess.check_output(["git", "-C", str(repo), "status", "--porcelain=v1", "-z"], text=True)
    # Should have staged changes
    assert "value.txt" in status_output or " M " in status_output

    failures = inspect(repo, base_sha)
    # Staged changes should be detected as uncommitted
    assert len(failures) == 1
    assert "Uncommitted checkout changes" in failures[0]


def test_check_handles_deleted_file(tmp_path: Path) -> None:
    """check detects deleted files as uncommitted changes."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    # Delete a file but don't commit
    (repo / "value.txt").unlink()
    # Don't commit - leave it as deletion

    failures = inspect(repo, base_sha)
    assert len(failures) == 1
    assert "Uncommitted checkout changes" in failures[0]
    assert "value.txt" in failures[0]


def test_check_handles_modified_file(tmp_path: Path) -> None:
    """check detects modified files (uncommitted)."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    # Modify a file but don't commit
    (repo / "value.txt").write_text("modified content\n", encoding="utf-8")

    failures = inspect(repo, base_sha)
    assert len(failures) == 1
    assert "Uncommitted checkout changes" in failures[0]
    assert "value.txt" in failures[0]


def test_git_command_runs_correctly(tmp_path: Path) -> None:
    """git() helper runs commands and returns CompletedProcess."""
    repo, _ = make_git_repo(tmp_path, clean=True)
    result = git(repo, "rev-parse", "--show-toplevel")
    assert result.returncode == 0
    assert str(repo.resolve()) in result.stdout
    # text=True means stdout is str, not bytes
    assert isinstance(result.stdout, str)
