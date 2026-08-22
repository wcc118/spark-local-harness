from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence, TextIO


class RunnerError(RuntimeError):
    pass


@dataclass(frozen=True)
class TestObservation:
    command: str
    status: str


@dataclass(frozen=True)
class RunResult:
    run_id: str
    role: str
    model: str
    repository: str
    branch: str
    head_sha: str
    exit_status: int
    elapsed_seconds: float
    log_file: str
    changed_paths: tuple[str, ...]
    dirty_paths: tuple[str, ...]
    tool_calls: int
    tool_errors: int
    tests: tuple[TestObservation, ...]


def _run_git(repository: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode:
        raise RunnerError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def _slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return value[:48] or "work"


def prepare_branch(repository: Path, issue: int | None, title: str | None) -> str:
    root = Path(_run_git(repository, "rev-parse", "--show-toplevel")).resolve()
    if root != repository.resolve():
        raise RunnerError(f"repository must be the Git root: {root}")
    current = _run_git(repository, "branch", "--show-current")
    if issue is None:
        return current
    branch = f"issue-{issue}-{_slug(title or 'work')}"
    if current == branch:
        # An interrupted worker intentionally leaves its branch and edits in
        # place. Running the same issue again is the resume operation.
        return branch
    if _run_git(repository, "status", "--porcelain=v1"):
        raise RunnerError("repository must be clean before switching to a different issue")
    existing = subprocess.run(
        ["git", "-C", str(repository), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
    )
    if existing.returncode == 0:
        _run_git(repository, "switch", branch)
    else:
        _run_git(repository, "switch", "-c", branch)
    return branch


def _tool_input(part: dict[str, Any]) -> dict[str, Any]:
    state = part.get("state") or {}
    value = state.get("input") or {}
    return value if isinstance(value, dict) else {}


def _describe_event(event: dict[str, Any]) -> str | None:
    event_type = event.get("type")
    part = event.get("part") or {}
    if event_type == "text" and isinstance(part.get("text"), str):
        return part["text"].rstrip()
    if event_type != "tool_use":
        return None
    tool = str(part.get("tool") or "tool")
    state = part.get("state") or {}
    status = str(state.get("status") or "unknown")
    inputs = _tool_input(part)
    subject = inputs.get("command") or inputs.get("filePath") or inputs.get("pattern") or ""
    subject = " ".join(str(subject).split())[:240]
    marker = "x" if status == "error" else "+"
    return f"[{marker}] {tool} {status}{': ' + subject if subject else ''}"


def _test_observations(events: Iterable[dict[str, Any]]) -> tuple[TestObservation, ...]:
    observations: list[TestObservation] = []
    for event in events:
        if event.get("type") != "tool_use":
            continue
        part = event.get("part") or {}
        if part.get("tool") != "bash":
            continue
        command = str(_tool_input(part).get("command") or "")
        if not re.search(r"(^|\s|/)(pytest|tox|nox|cargo test|go test|npm test|pnpm test|yarn test)(\s|$)", command):
            continue
        state = part.get("state") or {}
        observations.append(TestObservation(command, str(state.get("status") or "unknown")))
    return tuple(observations)


def _changed_paths(repository: Path, base_sha: str) -> tuple[str, ...]:
    committed = _run_git(repository, "diff", "--name-only", f"{base_sha}..HEAD").splitlines()
    dirty = _run_git(repository, "status", "--porcelain=v1").splitlines()
    dirty_paths = [line[3:].split(" -> ")[-1] for line in dirty if len(line) > 3]
    return tuple(dict.fromkeys([*committed, *dirty_paths]))


def run_worker(
    repository: Path,
    task: str,
    *,
    issue: int | None,
    title: str | None,
    role: str,
    model: str,
    agent: str,
    opencode: str,
    log_dir: Path,
    expected_head: str | None = None,
    output: TextIO = sys.stderr,
) -> RunResult:
    repository = repository.resolve()
    if role == "reviewer":
        branch = _run_git(repository, "branch", "--show-current")
        observed_head = _run_git(repository, "rev-parse", "HEAD")
        if expected_head is None:
            raise RunnerError("reviewer runs require --expected-head")
        if observed_head != expected_head:
            raise RunnerError(f"review head mismatch: expected {expected_head}, observed {observed_head}")
    else:
        branch = prepare_branch(repository, issue, title)
    base_sha = _run_git(repository, "rev-parse", "HEAD")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{run_id}.jsonl"
    started = time.monotonic()
    print(f"run {run_id} | {role} | {model} | {repository} | {branch}", file=output, flush=True)
    process = subprocess.Popen(
        [opencode, "run", "--format", "json", "--dir", str(repository), "--agent", agent, "--model", model, task],
        cwd=repository,
        env=os.environ.copy(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    events: list[dict[str, Any]] = []
    assert process.stdout is not None
    with log_file.open("w", encoding="utf-8") as retained:
        for line in process.stdout:
            retained.write(line)
            retained.flush()
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                print(line.rstrip(), file=output, flush=True)
                continue
            if isinstance(event, dict):
                events.append(event)
                description = _describe_event(event)
                if description:
                    print(description, file=output, flush=True)
    exit_status = process.wait()
    elapsed = time.monotonic() - started
    status_lines = _run_git(repository, "status", "--porcelain=v1").splitlines()
    dirty_paths = tuple(line[3:].split(" -> ")[-1] for line in status_lines if len(line) > 3)
    head_sha = _run_git(repository, "rev-parse", "HEAD")
    tool_events = [event for event in events if event.get("type") == "tool_use"]
    result = RunResult(
        run_id=run_id,
        role=role,
        model=model,
        repository=str(repository),
        branch=branch,
        head_sha=head_sha,
        exit_status=exit_status,
        elapsed_seconds=round(elapsed, 3),
        log_file=str(log_file),
        changed_paths=_changed_paths(repository, base_sha),
        dirty_paths=dirty_paths,
        tool_calls=len(tool_events),
        tool_errors=sum((event.get("part") or {}).get("state", {}).get("status") == "error" for event in tool_events),
        tests=_test_observations(events),
    )
    result_file = log_dir / f"{run_id}.result.json"
    result_file.write_text(json.dumps(asdict(result), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(asdict(result), sort_keys=True), file=output, flush=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run an unconstrained local OpenCode worker transparently")
    parser.add_argument("repository", type=Path)
    task_group = parser.add_mutually_exclusive_group(required=True)
    task_group.add_argument("--task")
    task_group.add_argument("--task-file", type=Path)
    parser.add_argument("--issue", type=int)
    parser.add_argument("--title")
    parser.add_argument("--role", choices=("coder", "reviewer"), default="coder")
    parser.add_argument("--expected-head", help="required exact commit for reviewer runs")
    parser.add_argument("--agent")
    parser.add_argument("--model", default=os.environ.get("CODEX_LOCAL_MODEL", "vllm/Qwen/Qwen3-Coder-Next-FP8"))
    parser.add_argument("--opencode", default=os.environ.get("CODEX_LOCAL_OPENCODE", "opencode"))
    parser.add_argument("--log-dir", type=Path, default=Path.home() / ".local/share/codex-local-harness/v5/runs")
    args = parser.parse_args(argv)
    task = args.task if args.task is not None else args.task_file.read_text(encoding="utf-8")
    agent = args.agent or f"v5-{args.role}"
    try:
        result = run_worker(
            args.repository, task, issue=args.issue, title=args.title, role=args.role,
            model=args.model, agent=agent, opencode=args.opencode, log_dir=args.log_dir,
            expected_head=args.expected_head,
        )
    except (OSError, RunnerError) as exc:
        parser.error(str(exc))
    return result.exit_status


if __name__ == "__main__":
    raise SystemExit(main())
