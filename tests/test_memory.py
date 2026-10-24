import json
from pathlib import Path
from unittest.mock import patch

from codex_local_harness.memory import configure, prepare, WORKER_TOOLS


def runtime(tmp_path):
    o=tmp_path/'.config/opencode';(o/'agents').mkdir(parents=True)
    (o/'opencode.json').write_text(json.dumps({'mcp':{'github':{'enabled':False}},'agent':{'reviewer':{'permission':{'*':'deny'}}},'instructions':['/home/u/projects/memory/MEMORY.md','keep.md']}))
    (o/'agents/reviewer.md').write_text('---\npermission:\n  "*": deny\n  read: allow\n---\nReview only.\n')
    (tmp_path/'.qwen').mkdir();(tmp_path/'.qwen/settings.json').write_text('{"tools":{"disabled":["web_fetch"]}}')
    return o


def test_role_credentials_and_both_runtimes(tmp_path):
    o=runtime(tmp_path)
    configure(tmp_path,'http://127.0.0.1:9602/mcp','contributor-token','reviewer')
    config=json.loads((o/'opencode.json').read_text())
    assert config['mcp']['github']['enabled'] is False
    assert config['mcp']['memory']['headers']['Authorization']=='Bearer contributor-token'
    assert config['instructions']==['keep.md']
    assert config['agent']['reviewer']['permission']['*']=='deny'
    assert config['agent']['reviewer']['permission']['memory_record_memory']=='allow'
    assert config['agent']['reviewer']['permission']['memory_resolve_memory']=='deny'
    assert 'memory_correct_memory: allow' in (o/'agents/reviewer.md').read_text()
    q=json.loads((tmp_path/'.qwen/settings.json').read_text())
    assert q['mcpServers']['memory']['includeTools']==list(WORKER_TOOLS)
    assert q['tools']['disabled']==['web_fetch']
    assert (o/'opencode.json').stat().st_mode & 0o777 == 0o600


def test_helper_remains_read_only(tmp_path):
    o=runtime(tmp_path);configure(tmp_path,'http://127.0.0.1:9602/mcp','reader','helper')
    assert 'memory_record_memory: deny' in (o/'agents/reviewer.md').read_text()
    q=json.loads((tmp_path/'.qwen/settings.json').read_text())
    assert q['mcpServers']['memory']['includeTools']==['search_memory','get_memory']


def test_missing_or_unavailable_memory_is_optional(tmp_path,capsys):
    runtime(tmp_path)
    assert not prepare(tmp_path/'absent',tmp_path,'coder','run','repo','1')
    config=tmp_path/'memory.json';config.write_text('{"url":"http://127.0.0.1:9602/mcp","issuer_token":"SECRET"}')
    with patch('urllib.request.urlopen',side_effect=OSError('SECRET')):
        assert not prepare(config,tmp_path,'coder','run','repo','1')
    assert 'SECRET' not in capsys.readouterr().err
    assert not (tmp_path/'memory-guidance.txt').exists()


def test_remote_issuer_url_rejected(tmp_path):
    runtime(tmp_path)
    config=tmp_path/'memory.json';config.write_text('{"url":"http://example.com/mcp","issuer_token":"SECRET"}')
    with patch('urllib.request.urlopen') as call:
        assert not prepare(config,tmp_path,'coder','run','repo','1')
        call.assert_not_called()


def test_effective_opencode_permissions(tmp_path):
    """Resolve real OpenCode policy: later wildcard denies must not hide memory."""
    import fnmatch
    import os
    import shutil
    import subprocess
    import pytest

    executable = shutil.which('opencode')
    if executable is None:
        pytest.skip('OpenCode CLI is required to resolve runtime permissions')
    o = runtime(tmp_path)
    (o/'opencode.json').write_text(json.dumps({'agent': {'reviewer': {'permission': {'*': 'deny'}}}}))
    env = {k: v for k, v in os.environ.items() if not k.startswith(('OPENCODE_', 'XDG_'))}
    env.update(HOME=str(tmp_path), XDG_CONFIG_HOME=str(tmp_path/'.config'),
               OPENCODE_CONFIG=str(o/'opencode.json'))
    for role in ['reviewer', 'helper', 'reviewer']:
        configure(tmp_path, 'http://127.0.0.1:9602/mcp', 'test-token', role)
        result = subprocess.run([executable, 'debug', 'agent', 'reviewer'],
            cwd=tmp_path, env=env, capture_output=True, text=True, check=True, timeout=30)
        rules = json.loads(result.stdout)['permission']
        expected = {'memory_search_memory': 'allow', 'memory_get_memory': 'allow',
                    'memory_record_memory': 'deny' if role == 'helper' else 'allow',
                    'memory_correct_memory': 'deny' if role == 'helper' else 'allow',
                    'memory_resolve_memory': 'deny', 'edit': 'deny'}
        for name, action in expected.items():
            matching = [r['action'] for r in rules if fnmatch.fnmatchcase(name, r['permission'])]
            assert matching[-1] == action, (role, name, matching)
        assert (o/'agents/reviewer.md').read_text().count('memory_record_memory:') == 1
