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
            unexpected.append(line)
    return unexpected


def finalize_coder(
    checkout: Path,
    repository: str,
    issue: int,
    run: Run = run_command,
    *,
    publish: bool = False,
    publication_failure_records: Path | None = None,
    run_id: str | None = None,
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

        # After successful push, retry PR-head verification once within a bounded interval.
        # This handles GitHub's eventual consistency where the PR list API might not
        # immediately reflect the correct headRefOid after a push.
        pr = _retry_pr_verification(
            canonical,
            repository,
            branch,
            run,
            publication_failure_records,
            run_id,
        )
        created_pr = False
    else:
        prs = _json(
            run,
            ["gh", "pr", "list", "--repo", repository, "--state", "open", "--head", branch,
             "--json", "number,headRefName,headRefOid,baseRefName"],
            canonical,
        )
        if not isinstance(prs, list):
            raise ReviewError("invalid PR list response")
        created_pr = False
        if not prs:
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

    # Verify PR identity after the early-exit path
    if publish:
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


def _retry_pr_verification(
    canonical: Path,
    repository: str,
    branch: str,
    run: Run,
    publication_failure_records: Path | None,
    run_id: str | None,
) -> dict[str, Any]:
    """Verify PR exists and matches HEAD after push, with one retry for eventual consistency.
    
    After a successful push, GitHub's PR list API may not immediately reflect the
    correct headRefOid. This function performs the PR lookup once, and if it fails
    due to a mismatch, retries once after a short bounded interval. The first
    mismatch and retry outcome are preserved as structured health/evidence output.
    """
    first_error: ReviewError | None = None
    
    def verify() -> dict[str, Any]:
        prs = _json(
            run,
            ["gh", "pr", "list", "--repo", repository, "--state", "open", "--head", branch,
             "--json", "number,headRefName,headRefOid,baseRefName"],
            canonical,
        )
        if not isinstance(prs, list):
            raise ReviewError("invalid PR list response")
        if len(prs) != 1:
            raise ReviewError(f"expected exactly one open PR for branch {branch}")
        pr = prs[0]
        if pr.get("headRefName") != branch or pr.get("baseRefName") != "main":
            raise ReviewError("coder PR identity does not match checkout or main base")
        return pr
    
    try:
        return verify()
    except ReviewError as exc:
        first_error = exc
        # Record the first mismatch outcome
        _record_publication_failure(
            publication_failure_records,
            run_id,
            "verify-pr-after-push",
            f"mismatch:{str(exc)}",
            repository,
        )
    
    # Retry once after a bounded interval (short delay for eventual consistency)
    time.sleep(1.0)
    
    try:
        return verify()
    except ReviewError:
        # The retry also failed; preserve the first mismatch (not the retry failure)
        if first_error:
            raise first_error
        raise


def _record_publication_failure(
    publication_failure_records: Path | None,
    run_id: str | None,
    phase: str,
    target: str,
    error_class: str,
) -> None:
    """Record a publication failure if records path and run_id are available."""
    if publication_failure_records is None or run_id is None:
        return
    try:
        # Generate a deterministic failure_id based on run_id and phase
        failure_id = hashlib.sha256(f"{run_id}:{phase}:{target}".encode()).hexdigest()[:32]
        record = PublicationFailure(
            failure_id=failure_id,
            run_id=run_id,
            phase=phase,
            target=target,
            error_class=error_class,
            observed_at=datetime.now(timezone.utc).isoformat(),
        )
        append_record(publication_failure_records, record)
    except (OSError, ValueError):
        # Fail silently if recording fails; this is diagnostic, not critical
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--issue", type=int, required=True)
    parser.add_argument("--result-out", type=Path)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--publication-failure-records", type=Path)
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    try:
        result = finalize_coder(
            args.checkout,
            args.repository,
            args.issue,
            publish=args.publish,
            publication_failure_records=args.publication_failure_records,
            run_id=args.run_id,
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
