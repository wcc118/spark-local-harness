from __future__ import annotations

import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from codex_local_harness.v5_runner import RunnerError, main, prepare_branch, run_worker


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "initial"], check=True, capture_output=True)
    return repo


def fake_opencode(tmp_path: Path) -> Path:
    executable = tmp_path / "fake-opencode"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "repo = pathlib.Path(sys.argv[sys.argv.index('--dir') + 1])\n"
        "(repo / 'environment.txt').write_text(os.environ.get('V5_SENTINEL', 'missing'))\n"
        "events = [\n"
        " {'type':'text','part':{'text':'working'}},\n"
        " {'type':'tool_use','part':{'tool':'bash','state':{'status':'completed','input':{'command':'python3 -m pytest'}}}},\n"
        "]\n"
        "for event in events: print(json.dumps(event), flush=True)\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def test_prepare_branch_happens_before_worker_execution(tmp_path):
    repo = repository(tmp_path)
    assert prepare_branch(repo, 99, "Thin transparent runner") == "issue-99-thin-transparent-runner"
    assert git(repo, "branch", "--show-current") == "issue-99-thin-transparent-runner"


def test_prepare_branch_refuses_unrelated_dirty_work(tmp_path):
    repo = repository(tmp_path)
    (repo / "README.md").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(RunnerError, match="must be clean"):
        prepare_branch(repo, 99, "runner")


def test_prepare_branch_resumes_dirty_matching_issue(tmp_path):
    repo = repository(tmp_path)
    branch = prepare_branch(repo, 99, "runner")
    (repo / "unfinished.py").write_text("work\n", encoding="utf-8")
    assert prepare_branch(repo, 99, "runner") == branch
    assert (repo / "unfinished.py").exists()


def test_runner_preserves_environment_streams_and_retains_evidence(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    logs = tmp_path / "logs"
    output = io.StringIO()
    monkeypatch.setenv("V5_SENTINEL", "visible-to-worker")

    result = run_worker(
        repo, "do the work", issue=99, title="Thin transparent runner", role="coder",
        model="test/model", agent="coder", opencode=str(fake_opencode(tmp_path)),
        log_dir=logs, output=output,
    )

    assert result.exit_status == 0
    assert result.branch == "issue-99-thin-transparent-runner"
    assert result.dirty_paths == ("environment.txt",)
    assert result.tests[0].command == "python3 -m pytest"
    assert result.tests[0].status == "completed"
    assert (repo / "environment.txt").read_text(encoding="utf-8") == "visible-to-worker"
    assert "working" in output.getvalue()
    assert "bash completed: python3 -m pytest" in output.getvalue()
    assert Path(result.log_file).read_text(encoding="utf-8").count("\n") == 2
    retained = json.loads((logs / f"{result.run_id}.result.json").read_text(encoding="utf-8"))
    assert retained["head_sha"] == git(repo, "rev-parse", "HEAD")


def test_interrupted_or_failed_run_leaves_worktree_for_inspection(tmp_path):
    repo = repository(tmp_path)
    executable = fake_opencode(tmp_path)
    text = executable.read_text(encoding="utf-8").replace(
        "for event in events: print(json.dumps(event), flush=True)",
        "for event in events: print(json.dumps(event), flush=True)\nsys.exit(23)",
    )
    executable.write_text(text, encoding="utf-8")

    result = run_worker(
        repo, "fail after editing", issue=99, title="runner", role="coder",
        model="test/model", agent="coder", opencode=str(executable),
        log_dir=tmp_path / "logs", output=io.StringIO(),
    )

    assert result.exit_status == 23
    assert (repo / "environment.txt").exists()
    assert result.dirty_paths == ("environment.txt",)


def test_runner_does_not_treat_model_prose_as_test_evidence(tmp_path):
    repo = repository(tmp_path)
    executable = fake_opencode(tmp_path)
    executable.write_text(
        executable.read_text(encoding="utf-8").replace(
            "{'type':'tool_use','part':{'tool':'bash','state':{'status':'completed','input':{'command':'python3 -m pytest'}}}},",
            "{'type':'text','part':{'text':'all tests pass'}},",
        ),
        encoding="utf-8",
    )
    result = run_worker(
        repo, "claim success", issue=99, title="runner", role="coder",
        model="test/model", agent="coder", opencode=str(executable),
        log_dir=tmp_path / "logs", output=io.StringIO(),
    )
    assert result.tests == ()


def test_reviewer_requires_exact_current_head_without_switching(tmp_path):
    repo = repository(tmp_path)
    head = git(repo, "rev-parse", "HEAD")
    result = run_worker(
        repo, "review", issue=99, title="ignored", role="reviewer",
        model="test/model", agent="reviewer", opencode=str(fake_opencode(tmp_path)),
        log_dir=tmp_path / "logs", expected_head=head, output=io.StringIO(),
    )
    assert result.branch == "main"
    assert result.head_sha == head
    with pytest.raises(RunnerError, match="review head mismatch"):
        run_worker(
            repo, "review", issue=99, title="ignored", role="reviewer",
            model="test/model", agent="reviewer", opencode=str(fake_opencode(tmp_path)),
            log_dir=tmp_path / "logs-2", expected_head="0" * 40, output=io.StringIO(),
        )


def test_cli_defaults_to_unrestricted_v5_agent(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    executable = fake_opencode(tmp_path)
    captured = tmp_path / "arguments.txt"
    executable.write_text(
        executable.read_text(encoding="utf-8").replace(
            "repo = pathlib.Path(sys.argv[sys.argv.index('--dir') + 1])",
            "repo = pathlib.Path(sys.argv[sys.argv.index('--dir') + 1])\n"
            f"pathlib.Path({str(captured)!r}).write_text(' '.join(sys.argv))",
        ),
        encoding="utf-8",
    )
    assert main([
        str(repo), "--task", "work", "--issue", "99", "--title", "runner",
        "--opencode", str(executable), "--model", "test/model",
        "--log-dir", str(tmp_path / "logs"),
    ]) == 0
    assert "--agent v5-coder" in captured.read_text(encoding="utf-8")
