"""The review preflight also fetches bases that advance after dispatch."""
from codex_local_harness.review import build_envelope
from test_base_resolution import git, history
import json


def test_standalone_review_materializes_new_base(history):
    remote, clone, first, tip = history
    git(clone, 'checkout', '-b', 'issue-7-fix')
    # Only GitHub responses and remote identity are synthetic; every other Git operation is real.
    def run(args, cwd=None):
        if args == ['git', 'remote', 'get-url', 'origin']:
            return 'https://github.com/owner/repo.git'
        if args[:3] == ['gh', 'pr', 'view']:
            return json.dumps({'number':11,'state':'OPEN','headRefName':'issue-7-fix',
                              'headRefOid':first,'baseRefName':'main'})
        if args[:3] == ['gh', 'issue', 'view']:
            return json.dumps({'number':7,'state':'OPEN','labels':[{'name':'status/review'}]})
        if args[:2] == ['gh', 'api']: return tip
        from codex_local_harness.review import run_command
        return run_command(args, cwd)
    envelope = build_envelope(clone, 'owner/repo', 7, 11, run=run)
    assert envelope.base_sha == tip
    git(clone, 'cat-file', '-e', tip+'^{commit}')
    assert envelope.head_sha == first
