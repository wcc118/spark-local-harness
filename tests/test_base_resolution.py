"""Exercise the production resolver against real local Git repositories."""
import json
import subprocess
from pathlib import Path

import pytest
from codex_local_harness import base_resolution as base


def git(path, *args):
    return subprocess.check_output(['git', *args], cwd=path, text=True,
                                   stderr=subprocess.DEVNULL).strip()


@pytest.fixture
def history(tmp_path):
    remote = tmp_path / 'remote'; remote.mkdir()
    git(remote, 'init', '-b', 'main')
    git(remote, 'config', 'user.name', 'Fixture')
    git(remote, 'config', 'user.email', 'fixture@example.invalid')
    (remote / 'base.txt').write_text('initial\n')
    git(remote, 'add', '.'); git(remote, 'commit', '-m', 'initial')
    first = git(remote, 'rev-parse', 'HEAD')
    clone = tmp_path / 'clone'
    git(tmp_path, 'clone', '--no-local', str(remote), str(clone))
    (remote / 'base.txt').write_text('advanced\n')
    git(remote, 'commit', '-am', 'advance')
    tip = git(remote, 'rev-parse', 'HEAD')
    return remote, clone, first, tip


def test_stale_ref_and_missing_object_are_repaired_by_production_resolver(history):
    remote, clone, first, tip = history
    assert git(clone, 'rev-parse', 'origin/main') == first
    with pytest.raises(subprocess.CalledProcessError):
        git(clone, 'cat-file', '-e', tip)
    calls = []
    def github(args):
        calls.append(args)
        assert args == ['gh', 'api', 'repos/owner/repo/git/ref/heads/main', '--jq', '.object.sha']
        return tip
    result = base.resolve_and_materialize_base('owner/repo', 'main', clone, gh_api_runner=github)
    assert result.exact_sha == tip
    assert len(calls) == 1
    assert git(clone, 'rev-parse', 'origin/main') == tip
    assert git(clone, 'rev-parse', 'HEAD') == first  # source/worker head is preserved
    git(clone, 'cat-file', '-e', tip + '^{commit}')


def test_existing_object_still_replaces_stale_remote_ref(history):
    remote, clone, first, tip = history
    git(clone, 'fetch', 'origin', tip)
    base.resolve_and_materialize_base('owner/repo', 'main', clone, gh_api_runner=lambda _:tip)
    assert git(clone, 'rev-parse', 'origin/main') == tip


def test_failed_fetch_fails_closed_without_repinning(history):
    remote, clone, first, tip = history
    git(clone, 'remote', 'set-url', 'origin', str(clone / 'missing'))
    with pytest.raises(base.BaseResolutionError):
        base.resolve_and_materialize_base('owner/repo', 'main', clone, gh_api_runner=lambda _:tip)
    assert git(clone, 'rev-parse', 'origin/main') == first


@pytest.mark.parametrize('sha', ['', 'a'*39, 'g'*40, 'a'*40+'\nextra'])
def test_invalid_github_identity_rejected(history, sha):
    _, clone, first, _ = history
    with pytest.raises(base.BaseResolutionError):
        base.resolve_and_materialize_base('owner/repo', 'main', clone, gh_api_runner=lambda _:sha)
    assert git(clone, 'rev-parse', 'origin/main') == first


def test_blob_is_not_accepted_as_commit(history):
    _, clone, _, _ = history
    blob = git(clone, 'rev-parse', 'HEAD:base.txt')
    with pytest.raises(base.BaseResolutionError):
        base.resolve_and_materialize_base('owner/repo', 'main', clone, gh_api_runner=lambda _:blob)


def test_reviewer_resolves_actual_stacked_base_through_cli(history, monkeypatch, capsys):
    remote, clone, _, tip = history
    git(remote, 'branch', 'stacked/base')
    real = base.run_command
    def runner(args, cwd):
        if args[0] != 'gh': return real(args, cwd)
        if args[1:3] == ['pr', 'view']:
            assert '--repo' in args
            return json.dumps({'baseRefName':'stacked/base'})
        assert 'repos/owner/repo/git/ref/heads/stacked/base' in args
        return tip
    monkeypatch.setattr(base, 'run_command', runner)
    assert base.main(['--checkout', str(clone), '--repository', 'owner/repo', '--role', 'reviewer', '--pr', '3']) == 0
    assert capsys.readouterr().out.strip() == 'stacked/base\t'+tip
    assert git(clone, 'rev-parse', 'origin/stacked/base') == tip


def test_worker_invokes_same_resolver_and_retains_pinned_identity():
    script = (Path(__file__).parents[1]/'bin/codex-local-worker-v4').read_text()
    assert 'python3 -m codex_local_harness.base_resolution' in script
    assert 'AUTHORITATIVE_BASE branch=$base_branch sha=$base_sha' in script
    assert 'coder_postflight_base_args+=(--base-sha "$base_sha")' in script
    assert script.index('python3 -m codex_local_harness.base_resolution') < script.index('review_prepare_args=()')
