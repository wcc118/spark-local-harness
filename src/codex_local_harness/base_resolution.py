"""Host-only resolution of GitHub base commits before worker inference."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

SHA40_PATTERN = re.compile(r'[0-9a-fA-F]{40}')


class BaseResolutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResolvedBase:
    branch: str
    exact_sha: str
    resolved_via: str


def run_command(args: Sequence[str], checkout: Path) -> str:
    try:
        result = subprocess.run(args, cwd=checkout, capture_output=True, text=True,
                                check=True, timeout=60)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise BaseResolutionError(f'Host base command failed: {args[0:3]}') from exc
    return result.stdout


def resolve_base_sha(repository, base_branch, gh_api_command):
    sha = gh_api_command(['gh', 'api',
        f'repos/{repository}/git/ref/heads/{base_branch}', '--jq', '.object.sha']).strip()
    if not SHA40_PATTERN.fullmatch(sha):
        raise BaseResolutionError('GitHub returned an invalid base SHA')
    return ResolvedBase(base_branch, sha, 'GitHub branch ref')


def materialize_missing_object(checkout_path, object_sha, fetch_command):
    if not SHA40_PATTERN.fullmatch(object_sha):
        raise BaseResolutionError('Invalid base SHA')
    probe = ['git', 'cat-file', '-e', object_sha + '^{commit}']
    try:
        fetch_command(probe)
        return
    except (BaseResolutionError, RuntimeError, subprocess.CalledProcessError):
        pass
    fetch_command(['git', 'fetch', '--no-tags', 'origin', object_sha])
    fetch_command(probe)


def pin_remote_ref(checkout_path, base_branch, exact_sha, git_command):
    if not SHA40_PATTERN.fullmatch(exact_sha):
        raise BaseResolutionError('Invalid base SHA')
    ref = 'refs/remotes/origin/' + base_branch
    git_command(['git', 'check-ref-format', ref])
    git_command(['git', 'update-ref', ref, exact_sha])


def resolve_and_materialize_base(repository, base_branch, checkout_path, *,
                                 gh_api_runner=None, fetch_runner=None, git_runner=None):
    run = lambda args: run_command(args, checkout_path)
    resolved = resolve_base_sha(repository, base_branch, gh_api_runner or run)
    materialize_missing_object(checkout_path, resolved.exact_sha, fetch_runner or run)
    pin_remote_ref(checkout_path, base_branch, resolved.exact_sha, git_runner or run)
    return resolved


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', required=True, type=Path)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--role', required=True, choices=['coder', 'reviewer'])
    parser.add_argument('--pr')
    args = parser.parse_args(argv)
    try:
        branch = 'main'
        if args.role == 'reviewer':
            if not args.pr:
                raise BaseResolutionError('Reviewer requires a PR')
            pr = json.loads(run_command(['gh', 'pr', 'view', args.pr, '--repo',
                args.repository, '--json', 'baseRefName'], args.checkout))
            branch = pr.get('baseRefName')
            if not isinstance(branch, str) or not branch:
                raise BaseResolutionError('PR has no base branch')
        resolved = resolve_and_materialize_base(args.repository, branch, args.checkout)
        print(f'{resolved.branch}\t{resolved.exact_sha}')
    except (BaseResolutionError, ValueError, OSError) as exc:
        parser.exit(65, f'Base resolution failed: {exc}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
