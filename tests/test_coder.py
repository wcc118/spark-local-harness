from __future__ import annotations

import json

import pytest

from codex_local_harness.coder import _unexpected_status, finalize_coder
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
