from __future__ import annotations

import json
import time
from pathlib import Path
from unittest import mock

import pytest

from codex_local_harness.coder import _unexpected_status, finalize_coder
from codex_local_harness.diagnostics import PublicationFailure, append_record
from codex_local_harness.review import ReviewError, WORKFLOW_LABELS


REPO = "wcc118/example"
SHA = "a" * 40


class CoderRun:
    def __init__(self, checkout, *, prs=1, status="", workflow=("status/review",)):
        self.checkout = checkout
        self.prs = prs
        self.status = status
        self.workflow = set(workflow)
        self.edits = []
        self.pushed = False
        self.created = False
        self.commented = False

    def __call__(self, argv, cwd=None):
        command = tuple(argv)
        if command == ("git", "rev-parse", "--show-toplevel"):
            return str(self.checkout)
        if command == ("git", "remote", "get-url", "origin"):
            return "https://github.com/wcc118/example.git\n"
        if command == ("git", "branch", "--show-current"):
            return "issue-7-fix\n"
        if command == ("git", "rev-parse", "HEAD"):
            return SHA + "\n"
        if command == ("git", "status", "--porcelain=v1"):
            return self.status
        if command[:2] == ("git", "push"):
            self.pushed = True
            return ""
        if command[:3] == ("gh", "pr", "list"):
            item = {"number": 11, "headRefName": "issue-7-fix", "headRefOid": SHA, "baseRefName": "main"}
            count = 1 if self.created else self.prs
            return json.dumps([item] * count)
        if command[:3] == ("gh", "pr", "create"):
            self.created = True
            return "https://github.com/wcc118/example/pull/11\n"
        if command[:3] == ("gh", "label", "list"):
            return json.dumps([{"name": name} for name in WORKFLOW_LABELS])
        if command[:3] == ("gh", "issue", "view"):
            if command[-1] == "number,state,title":
                return json.dumps({"number": 7, "state": "OPEN", "title": "Fix the cache"})
            return json.dumps({"number": 7, "state": "OPEN", "labels": [{"name": x} for x in self.workflow]})
        if command[:3] == ("gh", "issue", "edit"):
            self.edits.append(command)
            self.workflow = {"status/review"}
            return ""
        if command[:3] == ("gh", "issue", "comment"):
            self.commented = True
            return ""
        raise AssertionError(command)


def test_coder_postflight_accepts_exact_handoff(tmp_path):
    result = finalize_coder(tmp_path, REPO, 7, CoderRun(tmp_path))
    assert result["pull_request"] == 11


def test_coder_postflight_repairs_workflow_state_deterministically(tmp_path):
    runner = CoderRun(tmp_path, workflow=())
    result = finalize_coder(tmp_path, REPO, 7, runner)
    assert result["label"] == "status/review"
    assert runner.edits and "--add-label" in runner.edits[0]


def test_coder_postflight_rejects_multiple_prs(tmp_path):
    with pytest.raises(ReviewError, match="exactly one"):
        finalize_coder(tmp_path, REPO, 7, CoderRun(tmp_path, prs=2))


def test_coder_postflight_rejects_unexpected_dirty_file(tmp_path):
    with pytest.raises(ReviewError, match="unexpected checkout"):
        finalize_coder(tmp_path, REPO, 7, CoderRun(tmp_path, status="?? surprise.txt\n"))


def test_coder_publisher_owns_push_pr_comment_and_label(tmp_path):
    runner = CoderRun(tmp_path, prs=0, workflow=("status/in-progress",))
    result = finalize_coder(tmp_path, REPO, 7, runner, publish=True)
    assert result["published"] is True
    assert runner.pushed and runner.created and runner.commented
    assert runner.workflow == {"status/review"}


def test_generated_runtime_artifacts_are_not_unexpected():
    status = "?? issuebench/__pycache__/\n?? .pytest_cache/\n?? .coverage\n"
    assert _unexpected_status(status) == []


def test_coder_retry_succeeds_after_eventual_consistency_delay(tmp_path):
    """Test that after a successful push, PR verification retries on mismatch."""
    call_count = 0
    
    class RetryRun:
        def __init__(self, checkout):
            self.checkout = checkout
            self.pushed = False

        def __call__(self, argv, cwd=None):
            command = tuple(argv)
            if command == ("git", "rev-parse", "--show-toplevel"):
                return str(self.checkout)
            if command == ("git", "remote", "get-url", "origin"):
                return "https://github.com/wcc118/example.git\n"
            if command == ("git", "branch", "--show-current"):
                return "issue-85-fix\n"
            if command == ("git", "rev-parse", "HEAD"):
                return SHA + "\n"
            if command == ("git", "status", "--porcelain=v1"):
                return ""
            if command[:2] == ("git", "push"):
                self.pushed = True
                return ""
            if command[:3] == ("gh", "pr", "list"):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    # First call: PR exists but with wrong HEAD (simulating eventual consistency)
                    return json.dumps([{"number": 15, "headRefName": "issue-85-fix", "headRefOid": "x" * 40, "baseRefName": "main"}])
                else:
                    # Second call: PR now has correct HEAD (after retry delay)
                    return json.dumps([{"number": 15, "headRefName": "issue-85-fix", "headRefOid": SHA, "baseRefName": "main"}])
            if command[:3] == ("gh", "label", "list"):
                return json.dumps([{"name": name} for name in WORKFLOW_LABELS])
            if command[:3] == ("gh", "issue", "view"):
                return json.dumps({"number": 85, "state": "OPEN", "labels": [{"name": "status/review"}]})
            if command[:3] == ("gh", "issue", "edit"):
                return ""
            raise AssertionError(command)

    runner = RetryRun(tmp_path)
    with mock.patch("time.sleep", return_value=None):
        result = finalize_coder(tmp_path, REPO, 85, runner, publish=True)
    
    assert result["pull_request"] == 15
    assert runner.pushed
    assert call_count == 2  # First call failed, second call succeeded after retry


def test_coder_retry_fails_after_persistent_mismatch(tmp_path):
    """Test that persistent mismatch is recorded and raises after retry."""
    call_count = 0
    records_path = tmp_path / "failures.jsonl"
    Path(records_path).parent.mkdir(parents=True, exist_ok=True)
    
    class FailingRun:
        def __init__(self, checkout):
            self.checkout = checkout
            self.pushed = False

        def __call__(self, argv, cwd=None):
            command = tuple(argv)
            if command == ("git", "rev-parse", "--show-toplevel"):
                return str(self.checkout)
            if command == ("git", "remote", "get-url", "origin"):
                return "https://github.com/wcc118/example.git\n"
            if command == ("git", "branch", "--show-current"):
                return "issue-85-fix\n"
            if command == ("git", "rev-parse", "HEAD"):
                return SHA + "\n"
            if command == ("git", "status", "--porcelain=v1"):
                return ""
            if command[:2] == ("git", "push"):
                self.pushed = True
                return ""
            if command[:3] == ("gh", "pr", "list"):
                nonlocal call_count
                call_count += 1
                # Always return wrong HEAD (persistent mismatch)
                return json.dumps([{"number": 15, "headRefName": "issue-85-fix", "headRefOid": "x" * 40, "baseRefName": "main"}])
            if command[:3] == ("gh", "label", "list"):
                return json.dumps([{"name": name} for name in WORKFLOW_LABELS])
            if command[:3] == ("gh", "issue", "view"):
                return json.dumps({"number": 85, "state": "OPEN", "labels": [{"name": "status/review"}]})
            if command[:3] == ("gh", "issue", "edit"):
                return ""
            raise AssertionError(command)

    runner = FailingRun(tmp_path)
    run_id = "test-run-85"
    
    with mock.patch("time.sleep", return_value=None):
        with pytest.raises(ReviewError, match="coder PR identity"):
            finalize_coder(tmp_path, REPO, 85, runner, publish=True, 
                          publication_failure_records=records_path, run_id=run_id)
    
    assert runner.pushed
    assert call_count == 2  # First call failed, retry also failed
    
    # Verify the first mismatch was recorded as a PublicationFailure
    failures = read_records(records_path, PublicationFailure)
    assert len(failures) == 1
    failure = failures[0]
    assert failure.phase == "verify-pr-after-push"
    assert failure.run_id == run_id
    assert "mismatch" in failure.target


def read_records(path, record_type):
    """Read records from a JSONL file."""
    if not path.exists():
        return ()
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(record_type.from_dict(json.loads(line)))
    return tuple(records)


