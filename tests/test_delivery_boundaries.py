"""Exercise actual Git rejection and the production shell setup boundary."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from codex_local_harness import delivery_feedback as feedback
from codex_local_harness.coder import finalize_coder
from codex_local_harness.review import ReviewError, run_command


def repository(tmp_path):
    repo = tmp_path / 'repo'; repo.mkdir()
    for args in [('init','-b','issue-fixture'), ('config','user.name','Fixture'),
                 ('config','user.email','fixture@example.invalid')]:
        run_command(['git', *args], repo)
    (repo / 'file.txt').write_text('original\n')
    run_command(['git','add','.'], repo)
    run_command(['git','commit','-m','base'], repo)
    base = run_command(['git','rev-parse','HEAD'], repo).strip()
    run_command(['git','update-ref','refs/remotes/origin/main',base], repo)
    run_command(['git','remote','add','origin','https://github.com/test/repo.git'], repo)
    return repo, base


@pytest.mark.parametrize('failure', ['dirty', 'whitespace'])
def test_host_rejects_actual_git_failure_after_escalate(tmp_path, failure):
    repo, base = repository(tmp_path)
    (repo / 'file.txt').write_text('bad whitespace  \n')
    if failure == 'whitespace':
        run_command(['git','add','.'], repo)
        run_command(['git','commit','-m','bad whitespace'], repo)
        assert run_command(['git','status','--porcelain'], repo) == ''
    assert 'systemMessage' in feedback.check(repo, base, {
        'hook_event_name':'Stop', 'last_assistant_message':'ESCALATE: fixture'})
    calls = []
    def guarded_run(argv, cwd):
        calls.append(argv)
        assert argv[0] == 'git' and argv[1] not in {'push','fetch'}, argv
        return run_command(argv, cwd)
    expected = 'unexpected checkout changes' if failure == 'dirty' else 'trailing whitespace'
    with pytest.raises(ReviewError, match=expected):
        finalize_coder(repo, 'test/repo', 1, run=guarded_run, publish=True)
    assert any(argv[:2] == ['git','status'] for argv in calls)
    assert any(argv[:3] == ['git','diff','--check'] for argv in calls) == (failure == 'whitespace')


def test_unparseable_diff_reaches_diagnostic_branch(tmp_path, monkeypatch):
    repo, base = repository(tmp_path)
    actual_git = feedback.git
    seen = []
    def unparseable(checkout, *args):
        seen.append(args)
        if args[0] == 'diff':
            return subprocess.CompletedProcess(args, 1, 'unknown diagnostic', '')
        return actual_git(checkout, *args)
    monkeypatch.setattr(feedback, 'git', unparseable)
    with pytest.raises(ValueError, match='committed diff check could not be completed'):
        feedback.inspect(repo, base)
    assert any(args[0] == 'diff' for args in seen)
    assert 'systemMessage' in feedback.check(repo, base, {'hook_event_name':'Stop'})


def test_missing_comparison_commit_is_unavailable(tmp_path):
    repo, _ = repository(tmp_path)
    assert 'systemMessage' in feedback.check(repo, 'f'*40, {'hook_event_name':'Stop'})


@pytest.mark.parametrize('role,runtime,postflight,flag,broken,code,installed', [
    ('coder','qwen-code','1',None,False,0,True),
    ('coder','qwen-code','1','1',False,0,True),
    ('coder','qwen-code','1','0',False,0,False),
    ('reviewer','qwen-code','1','1',False,0,False),
    ('coder','opencode','1','1',False,0,False),
    ('coder','qwen-code','0','1',False,0,False),
    ('coder','qwen-code','1','invalid',False,64,False),
    ('coder','qwen-code','1','1',True,0,False),
])
def test_actual_shell_setup(tmp_path, role, runtime, postflight, flag, broken, code, installed):
    root = Path(__file__).resolve().parents[1]
    source = (root / 'bin/codex-local-worker-v4').read_text()
    block = source[source.index('# Local feedback is not publication authority'):source.index('# Optional role-scoped memory')]
    repo, _ = repository(tmp_path)
    home = tmp_path / 'home'; (home / '.qwen').mkdir(parents=True)
    settings = home / '.qwen/settings.json'
    settings.write_text('{"existing": true}')
    if broken:
        run_command(['git','update-ref','-d','refs/remotes/origin/main'], repo)
    env = os.environ | dict(worker_role=role, worker_runtime=runtime,
        CODEX_LOCAL_CODER_POSTFLIGHT=postflight, worker_repo_path=str(repo),
        worker_runtime_home=str(home), candidate_source=str(root/'src'), worker_task='fixture')
    env.pop('CODEX_LOCAL_DELIVERY_FEEDBACK', None)
    if flag is not None: env['CODEX_LOCAL_DELIVERY_FEEDBACK'] = flag
    result = subprocess.run(['bash','-eu','-c',block+'\nprintf "%s" "$worker_task"'],
                            env=env, capture_output=True, text=True)
    assert result.returncode == code, result.stderr
    data = json.loads(settings.read_text())
    assert data['existing'] is True
    assert bool(data.get('hooks',{}).get('Stop')) is installed
    assert ('LOCAL DELIVERY FEEDBACK:' in result.stdout) is installed
    assert ('WARN: local delivery feedback setup failed' in result.stderr) is broken
