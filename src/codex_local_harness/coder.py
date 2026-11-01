from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from .base_resolution import BaseResolutionError, materialize_missing_object
from .diagnostics import PublicationFailure, append_record
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
            unexpected.append(path)
    return unexpected


def _run_diff_check(base: str, head: str, run: Run, checkout: Path) -> None:
    """Run git diff --check on the comparison base and head."""
    revision = f"{base}..{head}"
    run(["git", "diff", "--check", revision], checkout)


def _list_prs(run: Run, canonical: Path, repository: str, branch: str) -> list[dict[str, Any]]:
    prs = _json(
        run,
        ["gh", "pr", "list", "--repo", repository, "--state", "open", "--head", branch,
         "--json", "number,headRefName,headRefOid,baseRefName"],
        canonical,
    )
    if not isinstance(prs, list) or not all(isinstance(item, dict) for item in prs):
        raise ReviewError("invalid PR list response")
    return prs


def _pr_observation(prs: list[dict[str, Any]]) -> str:
    if len(prs) != 1:
        return f"count-{len(prs)}"
    return str(prs[0].get("headRefOid") or "missing")


def _exact_pr(prs: list[dict[str, Any]], branch: str, head_sha: str) -> dict[str, Any]:
    if len(prs) != 1:
        raise ReviewError(f"expected exactly one open PR for branch {branch}")
    pr = prs[0]
    if pr.get("headRefName") != branch or pr.get("baseRefName") != "main" or pr.get("headRefOid") != head_sha:
        raise ReviewError("coder PR identity does not match checkout or main base")
    return pr


def _record_retry(
    records: Path | None,
    run_id: str | None,
    pr_number: int | None,
    expected: str,
    initial: str,
    retry: str,
    outcome: str,
) -> None:
    if records is None or run_id is None:
        return
    target = f"pr-{pr_number or 0}:{expected}:{initial}:{retry}"
    identity = hashlib.sha256(f"{run_id}:{target}:{outcome}".encode()).hexdigest()[:32]
    append_record(
        records,
        PublicationFailure(
            failure_id=identity,
            run_id=run_id,
            phase="coder-pr-head-retry",
            target=target,
            error_class=outcome,
            observed_at=datetime.now(timezone.utc).isoformat(),
        ),
    )


def _verify_with_one_retry(
    initial_prs: list[dict[str, Any]],
    canonical: Path,
    repository: str,
    branch: str,
    head_sha: str,
    run: Run,
    records: Path | None,
    run_id: str | None,
    delay: Callable[[float], None],
) -> tuple[dict[str, Any], dict[str, Any]]:
    initial = _pr_observation(initial_prs)
    try:
        pr = _exact_pr(initial_prs, branch, head_sha)
        return pr, {"attempts": 1, "initial": initial, "retry": None, "outcome": "matched"}
    except ReviewError as first_error:
        delay(1.0)
        retry_prs = _list_prs(run, canonical, repository, branch)
        retry = _pr_observation(retry_prs)
        pr_number = next((item.get("number") for item in retry_prs if isinstance(item.get("number"), int)), None)
        try:
            pr = _exact_pr(retry_prs, branch, head_sha)
        except ReviewError as retry_error:
            _record_retry(records, run_id, pr_number, head_sha, initial, retry, "persistent-mismatch")
            raise ReviewError(f"{first_error}; retry failed: {retry_error}") from retry_error
        _record_retry(records, run_id, pr_number, head_sha, initial, retry, "recovered")
        return pr, {"attempts": 2, "initial": initial, "retry": retry, "outcome": "recovered"}


def finalize_coder(
    checkout: Path,
    repository: str,
    issue: int,
    run: Run = run_command,
    *,
    publish: bool = False,
    publication_failure_records: Path | None = None,
    run_id: str | None = None,
    delay: Callable[[float], None] = time.sleep,
    base_sha: str | None = None,
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

    # Materialize the pinned base SHA if provided
    if base_sha is not None:
        materialize_missing_object(canonical, base_sha, lambda args: run(args, canonical))

    unexpected = _unexpected_status(run(["git", "status", "--porcelain=v1"], canonical))
    if unexpected:
        raise ReviewError(f"coder left unexpected checkout changes: {'; '.join(unexpected)}")

    if publish:
        # Use pinned base_sha if provided, otherwise resolve via merge-base
        diff_check_base = run(
            ["git", "merge-base", base_sha or "origin/main", head_sha], canonical
        ).strip()
        _run_diff_check(diff_check_base, head_sha, run, canonical)
        run(["git", "push", "--set-upstream", "origin", branch], canonical)

    prs = _list_prs(run, canonical, repository, branch)
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
        prs = _list_prs(run, canonical, repository, branch)

    verification: dict[str, Any] | None = None
    if publish:
        pr, verification = _verify_with_one_retry(
            prs, canonical, repository, branch, head_sha, run,
            publication_failure_records, run_id, delay,
        )
    else:
        pr = _exact_pr(prs, branch, head_sha)

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
        "publication_verification": verification,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--result-out", type=Path)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--publication-failure-records", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--base-sha", type=str, default=None,
                        help="Authoritative base SHA (pinned by host, skips merge-base resolution)")
    args = parser.parse_args(argv)
    try:
        result = finalize_coder(
            args.checkout,
            args.repository,
            args.issue,
            publish=args.publish,
            publication_failure_records=args.publication_failure_records,
            run_id=args.run_id,
            base_sha=args.base_sha,
        )
        if args.result_out:
            args.result_out.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ReviewError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 65


if __name__ == "__main__":
    raise SystemExit(main())
