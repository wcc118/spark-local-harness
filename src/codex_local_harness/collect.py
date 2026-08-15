from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Sequence

from .evidence import ArtifactEvidence, CheckEvidence, EvidenceBundle
from .review import normalize_remote


Run = Callable[[Sequence[str], Path | None], str]
GENERATED = re.compile(
    r"(^|/)(?:\.venv|venv|__pycache__|\.pytest_cache|[^/]+\.egg-info)(?:/|$)"
    r"|\.pyc$|(^|/)\.coverage(?:\.|$)"
)
MAX_DIFF_SUMMARY = 4000


class CollectionError(RuntimeError):
    pass


def run_command(argv: Sequence[str], cwd: Path | None = None) -> str:
    completed = subprocess.run(list(argv), cwd=cwd, check=True, text=True, capture_output=True)
    return completed.stdout


def collect_evidence(
    checkout: Path,
    repository: str,
    issue: int,
    pull_request: int,
    acceptance_criteria: tuple[str, ...],
    tests: tuple[CheckEvidence, ...],
    run: Run = run_command,
    *,
    worker_run_id: str | None = None,
) -> EvidenceBundle:
    if issue <= 0 or pull_request <= 0 or "/" not in repository:
        raise CollectionError("repository, issue, and pull request identity are required")
    canonical = checkout.resolve(strict=True)
    root = Path(run(["git", "rev-parse", "--show-toplevel"], canonical).strip()).resolve()
    if root != canonical:
        raise CollectionError("checkout must be the Git root")
    observed_remote = normalize_remote(run(["git", "remote", "get-url", "origin"], canonical))
    if observed_remote.casefold() != repository.casefold():
        raise CollectionError(f"remote mismatch: expected {repository}, observed {observed_remote}")
    status = run(["git", "status", "--porcelain=v1"], canonical)
    if status.strip():
        raise CollectionError("checkout must be clean before evidence collection")

    pr = _json(
        run,
        [
            "gh", "pr", "view", str(pull_request), "--repo", repository,
            "--json", "number,state,isDraft,headRefOid,baseRefName",
        ],
        canonical,
    )
    if pr.get("number") != pull_request or pr.get("state") != "OPEN" or pr.get("isDraft"):
        raise CollectionError("pull request is missing, not open, or a draft")
    if pr.get("baseRefName") != "main":
        raise CollectionError("pull request base must be main")
    head_sha = str(pr.get("headRefOid", ""))
    base_tip = run(
        ["gh", "api", f"repos/{repository}/git/ref/heads/{pr['baseRefName']}", "--jq", ".object.sha"],
        canonical,
    ).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", base_tip):
        raise CollectionError("GitHub returned an invalid base branch head")
    base_sha = run(["git", "merge-base", head_sha, base_tip], canonical).strip()
    if not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        raise CollectionError("Git returned an invalid pull request merge base")
    local_head = run(["git", "rev-parse", "HEAD"], canonical).strip()
    if local_head != head_sha:
        raise CollectionError("checkout head does not match the pull request head")

    issue_data = _json(
        run,
        ["gh", "issue", "view", str(issue), "--repo", repository, "--json", "number,state"],
        canonical,
    )
    if issue_data.get("number") != issue or issue_data.get("state") != "OPEN":
        raise CollectionError("issue is missing or not open")

    changed_files = tuple(
        line for line in run(["git", "diff", "--name-only", f"{base_sha}...{head_sha}"], canonical).splitlines()
        if line
    )
    if not changed_files:
        raise CollectionError("pull request has no changed files")
    generated = tuple(path for path in changed_files if GENERATED.search(path))
    if generated:
        raise CollectionError(f"tracked generated artifacts are prohibited: {list(generated[:25])}")
    summary = run(["git", "diff", "--stat", "--compact-summary", f"{base_sha}...{head_sha}"], canonical).strip()
    if not summary:
        raise CollectionError("Git returned an empty diff summary")
    if len(summary) > MAX_DIFF_SUMMARY:
        summary = summary[: MAX_DIFF_SUMMARY - 20] + "\n...[summary bounded]"
    return EvidenceBundle(
        repository=repository,
        issue=issue,
        base_sha=base_sha,
        head_sha=head_sha,
        acceptance_criteria=acceptance_criteria,
        changed_files=changed_files,
        diff_summary=summary,
        tests=tests,
        artifacts=ArtifactEvidence(
            tracked_count=len(changed_files),
            untracked_count=0,
            generated_count=0,
            sample=changed_files[:25],
        ),
        worker_run_id=worker_run_id,
    )


def _json(run: Run, argv: Sequence[str], cwd: Path) -> Any:
    try:
        return json.loads(run(argv, cwd))
    except json.JSONDecodeError as exc:
        raise CollectionError(f"invalid JSON from {' '.join(argv)}") from exc
