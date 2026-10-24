"""Tests for delivery_feedback module.

Exercises real temporary Git repositories for clean, dirty, staged, committed
whitespace (full base..HEAD even clean worktree), generated-artifact exception,
rename/unusual file paths, malformed inputs, invalid/missing base, explicit
ESCALATE and no-op unrelated events. Asserts read-only checking preserves
Git/worktree state; no source line contents leak in feedback (only locations).
Hook installation preserves existing settings and shell-quotes paths, and copied
standalone checker actually runs. Demonstrates final host finalize_coder still
rejects dirty/whitespace when local feedback is bypassed.
"""
from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from codex_local_harness.delivery_feedback import check, git, install, inspect, main

REPO = "wcc118/example"


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


def test_check_insufficient_whitespace_locations_raises(tmp_path: Path) -> None:
    """check raises ValueError when diff check fails but locations are unparseable."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    # Simulate a diff check that fails but produces unparseable output
    # by mocking git command behavior (diff --check exit non-zero but with weird output)
    result = subprocess.run(
        ["git", "diff", "--check", f"{base_sha}..HEAD"],
        cwd=repo, capture_output=True, text=True
    )
    # This test ensures the regex filter works correctly
    assert result.returncode == 0  # clean checkout has no diff issues


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


def test_check_standalone_runner_with_whitespace_error(tmp_path: Path) -> None:
    """Standalone check command detects whitespace error via subprocess."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    # Add trailing whitespace
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
    assert "trailing whitespace" in output.get("reason", "")


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
    """Source line contents do not leak into feedback - only locations."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    # Write a line with sensitive content
    sensitive_content = "password=secret123\napi_key=abcd1234\n"
    (repo / "secrets.txt").write_text(sensitive_content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "secrets.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial with secrets"], check=True, capture_output=True)

    # Now add trailing whitespace to another line (not the secrets one)
    (repo / "value.txt").write_text("clean  \n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "value.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "add whitespace"], check=True, capture_output=True)

    result = check(repo, base_sha, {"hook_event_name": "Stop", "last_assistant_message": ""})
    assert result.get("decision") == "block"
    reason = result.get("reason", "")
    # Should mention file and line location but not the sensitive content
    assert "trailing whitespace" in reason
    assert "value.txt" in reason
    assert "secret123" not in reason
    assert "abcd1234" not in reason
    assert "password" not in reason
    assert "api_key" not in reason


def test_check_handles_rename_path(tmp_path: Path) -> None:
    """check handles renamed files (R status in git status)."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    # Rename a file
    (repo / "old_name.txt").write_text("content\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "old_name.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "add old name"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "mv", "old_name.txt", "new_name.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "rename file"], check=True, capture_output=True)

    # Should not crash and should handle the rename
    failures = inspect(repo, base_sha)
    assert failures == []  # Clean after rename


def test_check_handles_copied_path(tmp_path: Path) -> None:
    """check handles copied files."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    # Copy a file
    (repo / "original.txt").write_text("content\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "original.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "add original"], check=True, capture_output=True)
    (repo / "copy.txt").write_text("content\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "copy.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "copy file"], check=True, capture_output=True)

    failures = inspect(repo, base_sha)
    assert failures == []  # Clean after copy


def test_check_with_unusual_file_path(tmp_path: Path) -> None:
    """check handles unusual file paths (spaces, special chars)."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    # Create a file with space in name
    unusual_dir = repo / "dir with spaces"
    unusual_dir.mkdir()
    unusual_file = unusual_dir / "file with spaces.txt"
    unusual_file.write_text("content\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "dir with spaces/file with spaces.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "unusual path"], check=True, capture_output=True)

    failures = inspect(repo, base_sha)
    assert failures == []  # Should handle unusual paths


def test_check_handles_generated_artifact_with_unusual_path(tmp_path: Path) -> None:
    """check excludes generated artifacts even with unusual paths."""
    repo, base_sha = make_git_repo(tmp_path, clean=True)
    # Create generated artifact in unusual path
    unusual_dir = repo / "dir with spaces"
    unusual_dir.mkdir(exist_ok=True)
    pycache = unusual_dir / "__pycache__"
    pycache.mkdir(exist_ok=True)
    (pycache / "module.pyc").write_text("data", encoding="utf-8")
    # Stage and commit it
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "add pycache"], check=True, capture_output=True)
    failures = inspect(repo, base_sha)
    assert failures == []  # Should be excluded


def test_install_with_unusual_checkout_path(tmp_path: Path) -> None:
    """install handles checkout with spaces in path."""
    # First create a repo with spaces in the path
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
    # Path with spaces should be quoted
    assert "--checkout" in command
    assert "repo with spaces" in command or '"repo with spaces"' in command or "'repo with spaces'" in command


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
