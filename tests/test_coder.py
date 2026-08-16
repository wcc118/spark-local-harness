from __future__ import annotations

import json

import pytest

from codex_local_harness.coder import _unexpected_status, finalize_coder
from codex_local_harness.diagnostics import PublicationFailure
from codex_local_harness.review import ReviewError, WORKFLOW_LABELS


REPO = "wcc118/example"
SHA = "a" * 40
STALE = "b" * 40


class CoderRun:
    def __init__(self, checkout, *, prs=1, heads=None, status="", workflow=("status/review",)):
        self.checkout = checkout
        self.prs = prs
        self.heads = list(heads or [])
        self.status = status
        self.workflow = set(workflow)
        self.edits = []
        self.pushed = False
        self.created = False
        self.commented = False
        self.pr_reads = 0

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
            self.pr_reads += 1
            count = 1 if self.created else self.prs
            head = self.heads.pop(0) if self.heads else SHA
            item = {"number": 11, "headRefName": "issue-7-fix", "headRefOid": head, "baseRefName": "main"}
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
    result = finalize_coder(tmp_path, REPO, 7, runner, publish=True, delay=lambda _: None)
    assert result["published"] is True
    assert runner.pushed and runner.created and runner.commented
    assert runner.workflow == {"status/review"}
    assert result["publication_verification"]["attempts"] == 1


def test_generated_runtime_artifacts_are_not_unexpected():
    status = "?? issuebench/__pycache__/\n?? .pytest_cache/\n?? .coverage\n"
    assert _unexpected_status(status) == []


def test_publisher_immediate_exact_head_does_not_retry(tmp_path):
    runner = CoderRun(tmp_path)
    result = finalize_coder(tmp_path, REPO, 7, runner, publish=True, delay=lambda _: None)
    assert runner.pr_reads == 1
    assert result["publication_verification"] == {
        "attempts": 1, "initial": SHA, "retry": None, "outcome": "matched",
    }


def test_publisher_retries_one_stale_head_and_records_recovery(tmp_path):
    records = tmp_path / "publication.jsonl"
    runner = CoderRun(tmp_path, heads=[STALE, SHA])
    result = finalize_coder(
        tmp_path, REPO, 7, runner, publish=True, delay=lambda _: None,
        publication_failure_records=records, run_id="run-85",
    )
    assert runner.pr_reads == 2
    assert result["publication_verification"] == {
        "attempts": 2, "initial": STALE, "retry": SHA, "outcome": "recovered",
    }
    event = PublicationFailure.from_dict(json.loads(records.read_text().strip()))
    assert event.error_class == "recovered"
    assert STALE in event.target and SHA in event.target


def test_publisher_fails_after_one_retry_and_records_both_observations(tmp_path):
    records = tmp_path / "publication.jsonl"
    runner = CoderRun(tmp_path, heads=[STALE, STALE])
    with pytest.raises(ReviewError, match="retry failed"):
        finalize_coder(
            tmp_path, REPO, 7, runner, publish=True, delay=lambda _: None,
            publication_failure_records=records, run_id="run-85",
        )
    assert runner.pushed and runner.pr_reads == 2 and not runner.created
    event = PublicationFailure.from_dict(json.loads(records.read_text().strip()))
    assert event.error_class == "persistent-mismatch"
    assert event.target.count(STALE) == 2 and SHA in event.target
