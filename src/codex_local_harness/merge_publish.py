from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

from .merge_gate import SCHEMA_VERSION


Run = Callable[[Sequence[str]], str]
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
WORKFLOW_LABELS = {
    "status/backlog",
    "status/in-progress",
    "status/review",
    "status/review-passed",
    "status/blocked",
    "status/done",
}


class MergePublishError(RuntimeError):
    pass


def run_command(argv: Sequence[str]) -> str:
    completed = subprocess.run(list(argv), check=True, text=True, capture_output=True)
    return completed.stdout


def _json(run: Run, argv: Sequence[str]) -> Any:
    try:
        return json.loads(run(argv))
    except json.JSONDecodeError as exc:
        raise MergePublishError(f"invalid JSON from {' '.join(argv)}") from exc


def load_merge_decision(path: Path, repository: str, issue: int) -> dict[str, Any]:
    try:
        decision = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MergePublishError(f"cannot read merge decision: {exc}") from exc
    if not isinstance(decision, dict):
        raise MergePublishError("merge decision must be an object")
    if decision.get("schema_version") != SCHEMA_VERSION:
        raise MergePublishError("unsupported merge decision schema")
    if decision.get("decision") != "merge" or decision.get("decided_by") != "codex":
        raise MergePublishError("merge requires an explicit Codex MERGE decision")
    if decision.get("repository", "").casefold() != repository.casefold() or decision.get("issue") != issue:
        raise MergePublishError("merge decision identity does not match repository and issue")
    if decision.get("deterministic_blockers"):
        raise MergePublishError("merge decision contains deterministic blockers")
    if not SHA_PATTERN.fullmatch(str(decision.get("reviewed_head_sha", ""))):
        raise MergePublishError("merge decision has invalid reviewed head")
    if not re.fullmatch(r"[0-9a-f]{64}", str(decision.get("evidence_sha256", ""))):
        raise MergePublishError("merge decision has invalid evidence digest")
    return decision


def publish_merge(
    repository: str,
    issue: int,
    pull_request: int,
    decision_path: Path,
    run: Run = run_command,
) -> dict[str, Any]:
    if issue <= 0 or pull_request <= 0 or "/" not in repository:
        raise MergePublishError("repository, issue, and pull request identity are required")
    decision = load_merge_decision(decision_path, repository, issue)
    reviewed_head = decision["reviewed_head_sha"]
    pr = _json(
        run,
        [
            "gh", "pr", "view", str(pull_request), "--repo", repository,
            "--json", "number,state,isDraft,headRefOid,mergeable,statusCheckRollup",
        ],
    )
    if pr.get("number") != pull_request or pr.get("state") != "OPEN":
        raise MergePublishError("pull request is missing or not open")
    if pr.get("isDraft"):
        raise MergePublishError("draft pull request cannot be merged")
    if pr.get("headRefOid") != reviewed_head:
        raise MergePublishError("pull request head changed after Codex review")
    if pr.get("mergeable") != "MERGEABLE":
        raise MergePublishError(f"pull request is not mergeable: {pr.get('mergeable')}")
    _validate_checks(pr.get("statusCheckRollup"))

    issue_data = _json(
        run,
        ["gh", "issue", "view", str(issue), "--repo", repository, "--json", "number,state,labels"],
    )
    if issue_data.get("number") != issue or issue_data.get("state") != "OPEN":
        raise MergePublishError("issue is missing or not open")

    run(["gh", "pr", "merge", str(pull_request), "--repo", repository, "--merge", "--delete-branch"])
    merged = _json(
        run,
        ["gh", "pr", "view", str(pull_request), "--repo", repository, "--json", "number,state,headRefOid,mergeCommit"],
    )
    if merged.get("state") != "MERGED" or merged.get("headRefOid") != reviewed_head:
        raise MergePublishError("GitHub did not confirm the reviewed head was merged")
    merge_commit = (merged.get("mergeCommit") or {}).get("oid")
    if not SHA_PATTERN.fullmatch(str(merge_commit or "")):
        raise MergePublishError("GitHub returned an invalid merge commit")

    post_merge_issue = _json(
        run,
        ["gh", "issue", "view", str(issue), "--repo", repository, "--json", "number,state,labels"],
    )
    if post_merge_issue.get("number") not in {None, issue} or post_merge_issue.get("state") not in {"OPEN", "CLOSED"}:
        raise MergePublishError("issue identity became invalid after merge")
    labels = {item.get("name") for item in post_merge_issue.get("labels", []) if isinstance(item, dict)}
    edit = ["gh", "issue", "edit", str(issue), "--repo", repository]
    for label in sorted((labels & WORKFLOW_LABELS) - {"status/done"}):
        edit.extend(["--remove-label", label])
    if "status/done" not in labels:
        edit.extend(["--add-label", "status/done"])
    if len(edit) > 7:
        run(edit)
    if post_merge_issue.get("state") == "OPEN":
        run(["gh", "issue", "close", str(issue), "--repo", repository, "--reason", "completed"])
    final_issue = _json(
        run,
        ["gh", "issue", "view", str(issue), "--repo", repository, "--json", "state,labels"],
    )
    final_labels = {item.get("name") for item in final_issue.get("labels", []) if isinstance(item, dict)}
    if final_issue.get("state") != "CLOSED" or (final_labels & WORKFLOW_LABELS) != {"status/done"}:
        raise MergePublishError("merged issue did not reach the required durable state")
    return {
        "repository": repository,
        "issue": issue,
        "pull_request": pull_request,
        "reviewed_head_sha": reviewed_head,
        "merge_commit": merge_commit,
        "decision_sha256": decision["evidence_sha256"],
        "issue_state": "CLOSED",
        "label": "status/done",
    }


def _validate_checks(checks: Any) -> None:
    if checks is None:
        raise MergePublishError("GitHub omitted status check information")
    if not isinstance(checks, list):
        raise MergePublishError("invalid status check information")
    for check in checks:
        if not isinstance(check, dict):
            raise MergePublishError("invalid status check entry")
        status = check.get("status")
        conclusion = check.get("conclusion")
        if status and status != "COMPLETED":
            raise MergePublishError("required checks are still running")
        if conclusion and conclusion not in {"SUCCESS", "NEUTRAL", "SKIPPED"}:
            raise MergePublishError(f"required check did not pass: {conclusion}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish a merge authorized by a Codex decision record")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--decision", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = publish_merge(args.repository, args.issue, args.pr, args.decision)
    except (MergePublishError, OSError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 65
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
