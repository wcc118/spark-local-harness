import json
import subprocess
from pathlib import Path

import pytest

from codex_local_harness.collect import CollectionError, collect_evidence
from codex_local_harness.evidence import CheckEvidence


HEAD = "b" * 40
BASE = "a" * 40


class FakeRun:
    def __init__(self, root, *, dirty=False, stale=False, generated=False):
        self.root = root
        self.dirty = dirty
        self.stale = stale
        self.generated = generated
        self.calls = []

    def __call__(self, argv, cwd=None):
        self.calls.append((list(argv), cwd))
        if argv == ["git", "rev-parse", "--show-toplevel"]:
            return str(self.root) + "\n"
        if argv == ["git", "remote", "get-url", "origin"]:
            return "https://github.com/owner/repo.git\n"
        if argv == ["git", "status", "--porcelain=v1"]:
            return "?? unexpected.txt\n" if self.dirty else ""
        if argv[:3] == ["gh", "pr", "view"]:
            assert "--repo" in argv
            return json.dumps({
                "number": 5, "state": "OPEN", "isDraft": False,
                "headRefOid": HEAD, "baseRefName": "main",
            })
        if argv[:2] == ["gh", "api"]:
            assert argv[2] == "repos/owner/repo/git/ref/heads/main"
            return BASE + "\n"
        if argv[:3] == ["gh", "issue", "view"]:
            assert "--repo" in argv
            return json.dumps({"number": 4, "state": "OPEN"})
        if argv == ["git", "rev-parse", "HEAD"]:
            return ("c" * 40 if self.stale else HEAD) + "\n"
        if argv == ["git", "merge-base", HEAD, BASE]:
            return BASE + "\n"
        if argv[:3] == ["git", "diff", "--name-only"]:
            return ".venv/bin/python\n" if self.generated else "src/value.py\ntests/test_value.py\n"
        if argv[:3] == ["git", "diff", "--stat"]:
            return " src/value.py | 4 ++++\n tests/test_value.py | 8 ++++++++\n 2 files changed, 12 insertions(+)\n"
        raise AssertionError(argv)


def collect(tmp_path, run):
    return collect_evidence(
        tmp_path,
        "owner/repo",
        4,
        5,
        ("Return the correct value",),
        (CheckEvidence("pytest -q", 0, "8 passed"),),
        run,
        worker_run_id="run-4",
    )


def test_collector_binds_clean_checkout_to_pr_and_issue(tmp_path):
    result = collect(tmp_path, FakeRun(tmp_path))
    assert result.head_sha == HEAD
    assert result.base_sha == BASE
    assert result.changed_files == ("src/value.py", "tests/test_value.py")
    assert result.tests_passed
    assert result.artifacts.sample == result.changed_files


def test_collector_rejects_dirty_or_stale_checkout(tmp_path):
    with pytest.raises(CollectionError, match="clean"):
        collect(tmp_path, FakeRun(tmp_path, dirty=True))
    with pytest.raises(CollectionError, match="does not match"):
        collect(tmp_path, FakeRun(tmp_path, stale=True))


def test_collector_rejects_tracked_generated_artifacts(tmp_path):
    with pytest.raises(CollectionError, match="generated artifacts"):
        collect(tmp_path, FakeRun(tmp_path, generated=True))


def test_collector_requires_checkout_to_be_git_root(tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(CollectionError, match="Git root"):
        collect(tmp_path, FakeRun(other))


def test_collector_integrates_with_a_disposable_git_repository(tmp_path):
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, text=True, capture_output=True).stdout

    git("init", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    (tmp_path / "value.py").write_text("VALUE = 1\n", encoding="utf-8")
    git("add", "value.py")
    git("commit", "-m", "base")
    base = git("rev-parse", "HEAD").strip()
    git("switch", "-c", "issue-4")
    (tmp_path / "value.py").write_text("VALUE = 2\n", encoding="utf-8")
    git("commit", "-am", "change")
    head = git("rev-parse", "HEAD").strip()
    git("remote", "add", "origin", "https://github.com/owner/repo.git")

    def run(argv, cwd=None):
        if argv[:3] == ["gh", "pr", "view"]:
            return json.dumps({
                "number": 5, "state": "OPEN", "isDraft": False,
                "headRefOid": head, "baseRefName": "main",
            })
        if argv[:2] == ["gh", "api"]:
            return base + "\n"
        if argv[:3] == ["gh", "issue", "view"]:
            return json.dumps({"number": 4, "state": "OPEN"})
        return subprocess.run(argv, cwd=cwd, check=True, text=True, capture_output=True).stdout

    result = collect_evidence(
        tmp_path,
        "owner/repo",
        4,
        5,
        ("Change the value",),
        (CheckEvidence("pytest -q", 0, "1 passed"),),
        run,
    )
    assert result.base_sha == base
    assert result.head_sha == head
    assert result.changed_files == ("value.py",)
