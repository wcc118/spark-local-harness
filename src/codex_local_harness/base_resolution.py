"""Authoritative GitHub base resolution with Git object materialization.

This module provides host-side utilities to:

1. Resolve the exact SHA for a GitHub branch/refs from the API (not from local clone)
2. Validate that the SHA is a 40-character lowercase hex
3. Fetch the exact object when it's absent from the local clone
4. Pin refs/remotes/origin/<base> to that exact SHA

The resolver runs **before** worker inference, ensuring:
- The local clone's remote-tracking refs are never trusted
- Missing base objects are fetched in host setup only
- No worker has network/auth capabilities; fetch happens only in host
- Clear failure before inference if resolution fails

Usage pattern (host setup, before isolation):

    # Resolve authoritative base SHA via GitHub API
    base_sha = resolve_base_sha(
        repository="owner/repo",
        base_branch="main",
        gh_api_command=["gh", "api", "repos/{repo}/git/ref/heads/{branch}", "--jq", ".object.sha"]
    )

    # Materialize the object if absent from local clone
    materialize_missing_object(
        checkout_path=Path("/path/to/clone"),
        object_sha=base_sha,
        fetch_command=["git", "fetch", "origin", f"{base_sha}:{base_sha}"]
    )

    # Pin the remote ref to the exact resolved SHA
    pin_remote_ref(
        checkout_path=Path("/path/to/clone"),
        base_branch="main",
        exact_sha=base_sha,
        git_command=["git", "update-ref"]
    )
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence


SHA40_PATTERN = re.compile(r"^[0-9a-f]{40}$")
Run = Callable[[Sequence[str], Path | None], str]


class BaseResolutionError(RuntimeError):
    """A deterministic base-resolution failure."""


@dataclass(frozen=True)
class ResolvedBase:
    """The authoritative base SHA and its source."""
    branch: str
    exact_sha: str
    resolved_via: str  # description of the resolution mechanism


def resolve_base_sha(
    repository: str,
    base_branch: str,
    gh_api_command: Callable[[Sequence[str]], str],
    *,
    fallback_to_merge_base: bool = False,
) -> ResolvedBase:
    """Resolve the authoritative base SHA via GitHub API.

    Args:
        repository: GitHub repo in owner/repo format
        base_branch: Branch name (e.g., "main", "master", "develop")
        gh_api_command: A runner that invokes gh api with given args, returns JSON text
        fallback_to_merge_base: If True, fall back to merge-base if API fails

    Returns:
        ResolvedBase with exact 40-char lowercase SHA

    Raises:
        BaseResolutionError: If resolution fails and no fallback
    """
    # GitHub API endpoint for branch ref
    api_args = [
        "gh", "api",
        f"repos/{repository}/git/ref/heads/{base_branch}",
        "--jq", ".object.sha"
    ]

    try:
        raw_sha = gh_api_command(api_args)
    except Exception as exc:
        if fallback_to_merge_base:
            # Caller can handle fallback; signal it wasn't resolved via API
            raise BaseResolutionError(f"API unavailable: {exc}") from exc
        raise BaseResolutionError(
            f"GitHub API unavailable for {repository}/{base_branch}: {exc}"
        ) from exc

    sha = raw_sha.strip()
    if not SHA40_PATTERN.fullmatch(sha):
        if fallback_to_merge_base:
            raise BaseResolutionError(
                f"Invalid SHA from API: {sha!r} (expected 40-char lowercase hex)"
            )
        raise BaseResolutionError(
            f"GitHub returned an invalid base SHA: {sha!r} (expected 40-char lowercase hex)"
        )

    return ResolvedBase(
        branch=base_branch,
        exact_sha=sha,
        resolved_via=f"GitHub API: repos/{repository}/git/ref/heads/{base_branch}",
    )


def materialize_missing_object(
    checkout_path: Path,
    object_sha: str,
    fetch_command: Callable[[Sequence[str]], str],
) -> None:
    """Fetch and materialize an object if it's absent from the local clone.

    First checks if the object exists locally (git cat-file -e).
    If absent, runs the provided fetch command.

    Args:
        checkout_path: Path to the local Git clone
        object_sha: The 40-char SHA to materialize
        fetch_command: A runner that invokes git fetch with given args

    Raises:
        BaseResolutionError: If fetch fails or object is still absent
    """
    if not SHA40_PATTERN.fullmatch(object_sha):
        raise BaseResolutionError(
            f"Invalid SHA for materialization: {object_sha!r}"
        )

    # Check if object exists locally
    try:
        fetch_command(["git", "cat-file", "-e", object_sha])
        # Object exists, nothing to do
        return
    except BaseResolutionError:
        # Object doesn't exist or cat-file failed; proceed to fetch
        pass

    # Fetch the exact object from remote
    try:
        fetch_command(["git", "fetch", "origin", object_sha])
    except Exception as exc:
        raise BaseResolutionError(
            f"Failed to fetch base object {object_sha} from origin: {exc}"
        ) from exc

    # Verify the object is now present
    try:
        fetch_command(["git", "cat-file", "-e", object_sha])
    except Exception as exc:
        raise BaseResolutionError(
            f"Base object {object_sha} not present after fetch: {exc}"
        ) from exc


def pin_remote_ref(
    checkout_path: Path,
    base_branch: str,
    exact_sha: str,
    git_command: Callable[[Sequence[str]], str],
) -> None:
    """Pin refs/remotes/origin/<base_branch> to the exact resolved SHA.

    This ensures that subsequent git operations use the authoritative SHA,
    not a potentially stale remote-tracking ref.

    Args:
        checkout_path: Path to the local Git clone
        base_branch: Branch name to pin (e.g., "main")
        exact_sha: The 40-char SHA to pin
        git_command: A runner that invokes git with given args

    Raises:
        BaseResolutionError: If the pin operation fails
    """
    if not SHA40_PATTERN.fullmatch(exact_sha):
        raise BaseResolutionError(f"Invalid SHA for pinning: {exact_sha!r}")

    ref_name = f"refs/remotes/origin/{base_branch}"

    try:
        # Update the remote ref to the exact SHA
        git_command(["git", "update-ref", ref_name, exact_sha])
    except Exception as exc:
        raise BaseResolutionError(
            f"Failed to pin {ref_name} to {exact_sha}: {exc}"
        ) from exc


def resolve_and_materialize_base(
    repository: str,
    base_branch: str,
    checkout_path: Path,
    *,
    gh_api_runner: Callable[[Sequence[str]], str] | None = None,
    fetch_runner: Callable[[Sequence[str]], str] | None = None,
    git_runner: Callable[[Sequence[str]], str] | None = None,
) -> ResolvedBase:
    """Convenience function that resolves, materializes, and pins in sequence.

    This is the main entry point for host-side base resolution. It:
    1. Resolves the base SHA via GitHub API
    2. Materializes the object if absent
    3. Pins the remote ref

    Args:
        repository: GitHub repo in owner/repo format
        base_branch: Branch name to resolve
        checkout_path: Path to the local Git clone
        gh_api_runner: Optional override for GitHub API runner
        fetch_runner: Optional override for fetch runner
        git_runner: Optional override for git runner

    Returns:
        ResolvedBase with the exact resolved SHA
    """
    # Set default runners
    def default_gh_api(args: Sequence[str]) -> str:
        result = subprocess.run(
            args, capture_output=True, text=True, cwd=checkout_path, check=False
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise BaseResolutionError(
                f"gh api failed ({result.returncode}): {' '.join(args)}: {detail}"
            )
        return result.stdout

    def default_fetch(args: Sequence[str]) -> str:
        result = subprocess.run(
            args, capture_output=True, text=True, cwd=checkout_path, check=False
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise BaseResolutionError(
                f"git fetch failed ({result.returncode}): {' '.join(args)}: {detail}"
            )
        return result.stdout

    def default_git(args: Sequence[str]) -> str:
        result = subprocess.run(
            args, capture_output=True, text=True, cwd=checkout_path, check=False
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise BaseResolutionError(
                f"git failed ({result.returncode}): {' '.join(args)}: {detail}"
            )
        return result.stdout

    gh_api = gh_api_runner or default_gh_api
    fetch_cmd = fetch_runner or default_fetch
    git_cmd = git_runner or default_git

    # Step 1: Resolve via GitHub API
    resolved = resolve_base_sha(repository, base_branch, gh_api)

    # Step 2: Materialize if missing
    materialize_missing_object(checkout_path, resolved.exact_sha, fetch_cmd)

    # Step 3: Pin the remote ref
    pin_remote_ref(checkout_path, base_branch, resolved.exact_sha, git_cmd)

    return resolved


def verify_base_sha_in_checkout(
    checkout_path: Path,
    base_sha: str,
    git_runner: Callable[[Sequence[str]], str] | None = None,
) -> bool:
    """Check if the base SHA exists in the local checkout.

    Returns:
        True if the object exists, False otherwise
    """
    if not SHA40_PATTERN.fullmatch(base_sha):
        return False

    def default_git(args: Sequence[str]) -> str:
        result = subprocess.run(
            args, capture_output=True, text=True, cwd=checkout_path, check=False
        )
        return result

    git_cmd = git_runner or default_git

    try:
        result = git_cmd(["git", "cat-file", "-e", base_sha])
        return result.returncode == 0
    except Exception:
        return False
