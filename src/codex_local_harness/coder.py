from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

from .review import WORKFLOW_LABELS, ReviewError, normalize_remote, run_command


Run = Callable[[Sequence[str], Path | None], str]
GENERATED = re.compile(r"(^|/)(?:__pycache__|\.pytest_cache)(?:/|$)|\.pyc$|(^|/)\.coverage(?:\.|$)")


def _json(run: Run, argv: Sequence[str], cwd: Path | None = None) -> Any:
    try:
        return json.loads(run(argv, cwd))
    except json.JSONDecodeError as exc:
        raise ReviewError(f"invalid JSON from {' '.join(argv)}") from exc


def _unexpected_status(status: str) -> list[str]:
    unexpected: list[str] = []
    for line in status.splitlines():
        path = line[3:].strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1]
        if not GENERATED.search(path):
            unexpected.append(line)
    return unexpected


def finalize_coder(
    checkout: Path,
    repository: str,
    issue: int,
    run: Run = run_command,
    *,
    publish: bool = False,
) -> dict[str, Any]:
    canonical = checkout.resolve(strict=True)
    root = Path(run(["git", "rev-parse", "--show-toplevel"], canonical).strip()).resolve()
    if root != canonical:
        raise ReviewError("checkout must be the Git root")
    remote = normalize_remote(run(["git", "remote", "get-url", "origin"], canonical))
    if remote.casefold() != repository.casefold():
        raise ReviewError(f"remote mismatch: expected {repository}, observed {remote}")
    branch = run(["git", "branch", "--show-current"], canonical).strip()
    head_sha = run(["git", "rev-parse", "HEAD"], canonical).strip()
    if not branch or not re.fullmatch(r"[0-9a-fA-F]{40}", head_sha):
        raise ReviewError("coder checkout has invalid branch or HEAD")
    unexpected = _unexpected_status(run(["git", "status", "--porcelain=v1"], canonical))
    if unexpected:
        raise ReviewError(f"coder left unexpected checkout changes: {unexpected}")

    if publish:
        run(["git", "push", "--set-upstream", "origin", branch], canonical)

    prs = _json(
        run,
        ["gh", "pr", "list", "--repo", repository, "--state", "open", "--head", branch,
         "--json", "number,headRefName,headRefOid,baseRefName"],
        canonical,
    )
    if not isinstance(prs, list):
        raise ReviewError("invalid PR list response")
    created_pr = False
    if publish and not prs:
        issue_identity = _json(
            run,
            ["gh", "issue", "view", str(issue), "--repo", repository, "--json", "number,state,title"],
            canonical,
        )
        if issue_identity.get("number") != issue or issue_identity.get("state") != "OPEN":
            raise ReviewError("issue is missing or not open")
        title = str(issue_identity.get("title") or f"Issue #{issue}")
        run(
            ["gh", "pr", "create", "--repo", repository, "--base", "main", "--head", branch,
             "--title", title, "--body", f"Closes #{issue}\n\nPublished by the deterministic coder handoff."],
            canonical,
        )
        created_pr = True
        prs = _json(
            run,
            ["gh", "pr", "list", "--repo", repository, "--state", "open", "--head", branch,
             "--json", "number,headRefName,headRefOid,baseRefName"],
            canonical,
        )
    if len(prs) != 1:
        raise ReviewError(f"expected exactly one open PR for branch {branch}")
    pr = prs[0]
    if pr.get("headRefName") != branch or pr.get("headRefOid") != head_sha or pr.get("baseRefName") != "main":
        raise ReviewError("coder PR identity does not match checkout or main base")

    labels = _json(run, ["gh", "label", "list", "--repo", repository, "--limit", "100", "--json", "name"], canonical)
    available = {item.get("name") for item in labels if isinstance(item, dict)}
    missing = WORKFLOW_LABELS - available
    if missing:
        raise ReviewError(f"repository is missing workflow labels: {sorted(missing)}")
    issue_data = _json(
        run,
        ["gh", "issue", "view", str(issue), "--repo", repository, "--json", "number,state,labels"],
        canonical,
    )
    if issue_data.get("number") != issue or issue_data.get("state") != "OPEN":
        raise ReviewError("issue is missing or not open")
    current = {item.get("name") for item in issue_data.get("labels", []) if isinstance(item, dict)}
    argv = ["gh", "issue", "edit", str(issue), "--repo", repository]
    for label in sorted((current & WORKFLOW_LABELS) - {"status/review"}):
        argv.extend(["--remove-label", label])
    if "status/review" not in current:
        argv.extend(["--add-label", "status/review"])
    if len(argv) > 6:
        run(argv, canonical)
    observed = _json(
        run,
        ["gh", "issue", "view", str(issue), "--repo", repository, "--json", "labels"],
        canonical,
    )
    workflow = {item.get("name") for item in observed.get("labels", []) if isinstance(item, dict)} & WORKFLOW_LABELS
    if workflow != {"status/review"}:
        raise ReviewError(f"coder handoff did not reach exactly status/review: {sorted(workflow)}")
    if created_pr:
        run(
            ["gh", "issue", "comment", str(issue), "--repo", repository, "--body",
             f"PR #{pr['number']} is ready for independent review at commit `{head_sha}`. "
             "Remote publication and workflow state were produced by the deterministic harness."],
            canonical,
        )
    return {
        "repository": repository,
        "issue": issue,
        "pull_request": pr["number"],
        "head_sha": head_sha,
        "label": "status/review",
        "published": publish,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--result-out", type=Path)
    parser.add_argument("--publish", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = finalize_coder(args.checkout, args.repository, args.issue, publish=args.publish)
        if args.result_out:
            args.result_out.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ReviewError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 65


if __name__ == "__main__":
    raise SystemExit(main())
