import json
import os
import shlex
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def install(home, action='install', check=True):
    return subprocess.run([str(ROOT / 'bin/install-harness'), action, '--home', str(home)],
                          text=True, capture_output=True, check=check)


def test_fresh_install_runs_without_source_checkout_and_rolls_back(tmp_path):
    home = tmp_path / 'user with spaces'
    install(home)
    state = json.loads((home / '.local/share/codex-local-harness/installation.json').read_text())
    release = Path(state['release'])
    install(home, 'verify')
    for command in ('codex-local-supervise', 'codex-local-completion'):
        result = subprocess.run([str(home / '.local/bin' / command), '--help'],
                                env=os.environ | {'PYTHONPATH': '/nonexistent'},
                                text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
    worker = home / '.local/bin/codex-local-worker'
    result = subprocess.run([str(worker)], text=True, capture_output=True)
    assert result.returncode == 2
    assert 'usage:' in result.stderr
    assert (release / 'agents/convergence-reviewer.md').is_file()
    install(home, 'rollback')
    assert not worker.exists()
    assert not (home / '.local/share/codex-local-harness/runtime/current').exists()


def test_upgrade_preserves_routes_and_restores_exact_predecessor(tmp_path):
    worker = tmp_path / '.local/bin/codex-local-worker'
    worker.parent.mkdir(parents=True)
    worker.write_text('#!/bin/sh\necho predecessor\n')
    worker.chmod(0o751)
    routes = tmp_path / '.config/codex-local/workers.json'
    routes.parent.mkdir(parents=True)
    routes.write_text('{"existing": true}\n')
    install(tmp_path)
    assert routes.read_text() == '{"existing": true}\n'
    install(tmp_path, 'rollback')
    assert worker.read_text() == '#!/bin/sh\necho predecessor\n'
    assert worker.stat().st_mode & 0o777 == 0o751
    assert routes.read_text() == '{"existing": true}\n'


def test_verify_and_rollback_reject_modified_wrapper(tmp_path):
    install(tmp_path)
    worker = tmp_path / '.local/bin/codex-local-worker'
    worker.write_text('operator change\n')
    assert install(tmp_path, 'verify', check=False).returncode != 0
    assert install(tmp_path, 'rollback', check=False).returncode != 0
    assert worker.read_text() == 'operator change\n'


def test_installed_coder_dispatches_supervised_without_default_deadline(tmp_path, fake_base_api):
    home = tmp_path / 'home'
    install(home)
    repo = tmp_path / 'repo'
    subprocess.run(['git', 'init', '-b', 'issue-180-smoke', str(repo)], check=True, capture_output=True)
    (repo / 'value.txt').write_text('original\n')
    for key, value in [('user.name', 'Test'), ('user.email', 'test@example.invalid')]:
        subprocess.run(['git', '-C', str(repo), 'config', key, value], check=True)
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-m', 'seed'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(repo), 'remote', 'add', 'origin', 'https://github.com/example/project.git'], check=True)
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    fake = fake_bin / 'opencode'
    deadline = tmp_path / 'deadline'
    fake.write_text('#!/bin/sh\ncat /proc/$PPID/cmdline > ' + shlex.quote(str(deadline)) +
                    '\nwhile [ $# -gt 0 ]; do [ "$1" = --dir ] && { shift; target=$1; }; shift; done\nprintf "changed\\n" > "$target/value.txt"\n')
    fake.chmod(0o755)
    routes = tmp_path / 'routes.json'
    routes.write_text(json.dumps({'defaults': {'coder': 'standard'}, 'routes': {
        'coder/standard': {'agent': 'coder', 'model': 'vllm/test', 'runtime': 'opencode', 'mode': 'native'}}}))
    health = tmp_path / 'health'
    env = os.environ | {'PATH': str(fake_bin) + ':' + os.environ['PATH'],
        'CODEX_LOCAL_WORKER_CONFIG': str(routes), 'CODEX_LOCAL_OPENCODE': str(fake),
        'CODEX_LOCAL_OPENCODE_CONFIG': str(home / '.config/codex-local/opencode-worker.json'),
        'CODEX_LOCAL_MODEL_SWITCH': '0', 'CODEX_LOCAL_SANDBOX': 'disabled',
        'CODEX_LOCAL_ALLOW_UNSANDBOXED': '1', 'CODEX_LOCAL_RECORD_RUN': '1',
        'CODEX_THREAD_ID': 'install-test', 'CODEX_LOCAL_SESSION_ID': 'install-test',
        'CODEX_LOCAL_REPOSITORY': 'example/project', 'CODEX_LOCAL_ISSUE': '180',
        'CODEX_LOCAL_HEALTH_DIR': str(health), 'CODEX_LOCAL_LOG_DIR': str(tmp_path / 'logs'),
        'CODEX_LOCAL_ISOLATION_PARENT': str(tmp_path / 'isolated'), 'CODEX_LOCAL_CODER_POSTFLIGHT': '0'}
    env.pop('CODEX_LOCAL_TIMEOUT_SECONDS', None)
    result = subprocess.run([str(home / '.local/bin/codex-local-coder'), str(repo), 'Edit value.txt.'],
                            env=env, text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert b'--kill-after=30s\x000\x00' in deadline.read_bytes()
    ownership = json.loads((health / 'ownership.jsonl').read_text())
    assert (Path(ownership['checkout']) / 'value.txt').read_text() == 'changed\n'
    assert (repo / 'value.txt').read_text() == 'original\n'
    terminal = json.loads((health / 'execution.jsonl').read_text())
    assert terminal['exit_status'] == 0
