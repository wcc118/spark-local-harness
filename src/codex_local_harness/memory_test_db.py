"""Host-owned disposable SurrealDB capability. Never loads production credentials."""
from __future__ import annotations
import argparse
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import time
import urllib.error
import urllib.request

IMAGE = 'surrealdb/surrealdb:v3.3.4@sha256:649a60a3b9c0177d9923c938e1c2563e48cde2a9d06ac2c0519714be719b02af'
LABEL = 'org.codex-local.memory-test'


class CapabilityError(RuntimeError):
    pass


def identity(run_id: str) -> str:
    if not run_id or len(run_id) > 200 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in run_id):
        raise CapabilityError('Invalid harness run identity')
    return hashlib.sha256(f'{os.getuid()}:{run_id}'.encode()).hexdigest()[:24]


def docker(*args, env=None, check=True):
    try:
        result = subprocess.run(['docker', *args], capture_output=True, text=True,
                                timeout=45, env=env)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CapabilityError('Disposable database Docker operation unavailable or timed out') from error
    if check and result.returncode:
        # Do not reflect arbitrary Docker stderr or environment into retained worker logs.
        raise CapabilityError(f'Disposable database Docker {args[0]} failed (exit {result.returncode})')
    return result


def owned(kind, name, key):
    result = docker(kind, 'inspect', name, check=False)
    if result.returncode:
        # Distinguish absent object from an unavailable daemon; never report cleanup success blindly.
        docker('info', '--format', '{{.ServerVersion}}')
        return False
    value = json.loads(result.stdout)[0]
    labels = value.get('Labels', {}) if kind == 'network' else value.get('Config', {}).get('Labels', {})
    if (labels or {}).get(LABEL) != key:
        raise CapabilityError('Refusing to remove a database resource without matching ownership label')
    return True


def cleanup(run_id):
    key = identity(run_id)
    name = 'codex-memory-test-' + key
    if owned('container', name, key):
        docker('container', 'rm', '--force', '--volumes', name)
    if owned('network', name, key):
        docker('network', 'rm', name)


def start(run_id, profile: Path):
    key = identity(run_id)
    name = 'codex-memory-test-' + key
    if profile.exists() or profile.is_symlink():
        raise CapabilityError('Disposable profile must be a new file')
    # Reject collisions before entering the cleanup scope: never tear down another active invocation.
    for kind in ('container', 'network'):
        if docker(kind, 'inspect', name, check=False).returncode == 0:
            raise CapabilityError('Disposable run resources already exist; inspect before explicit cleanup')
    docker('image', 'inspect', IMAGE)
    password = secrets.token_urlsafe(32)
    try:
        docker('network', 'create', '--internal', '--label', f'{LABEL}={key}', name)
        docker('run', '--detach', '--name', name, '--network', name,
               '--label', f'{LABEL}={key}', '--read-only', '--cap-drop', 'ALL',
               '--security-opt', 'no-new-privileges', '--user', '65534:65534',
               '--memory', '512m', '--cpus', '1', '--pids-limit', '128',
               '--tmpfs', '/tmp:rw,nosuid,nodev,noexec,size=64m',
               '--tmpfs', '/data:rw,nosuid,nodev,noexec,size=64m',
               '--tmpfs', '/logs:rw,nosuid,nodev,noexec,size=16m',
               '--env', 'SURREAL_USER', '--env', 'SURREAL_PASS', IMAGE,
               'start', '--bind', '0.0.0.0:8000', '--no-banner', '--log', 'warn',
               '--query-timeout', '20s', '--transaction-timeout', '20s',
               '--deny-net', '--', 'memory',
               env=os.environ | {'SURREAL_USER': 'memory_test', 'SURREAL_PASS': password})
        inspect = json.loads(docker('container', 'inspect', name).stdout)[0]
        address = inspect['NetworkSettings']['Networks'][name]['IPAddress']
        ip = ipaddress.ip_address(address)
        if ip.version != 4 or not ip.is_private or ip.is_loopback:
            raise CapabilityError('Unexpected disposable network address')
        endpoint = f'http://{address}:8000'
        ready = False
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(endpoint + '/health', timeout=1) as response:
                    ready = response.status == 200
                if ready:
                    break
            except (OSError, urllib.error.URLError):
                time.sleep(0.2)
        if not ready:
            raise CapabilityError('Disposable database did not become ready')
        payload = dict(schema=1, disposable=True, endpoint=endpoint,
                       namespace='memory_test', username='memory_test', password=password)
        with profile.open('x', encoding='utf-8') as stream:
            os.chmod(profile, 0o600)
            json.dump(payload, stream)
        return name
    except BaseException:
        cleanup(run_id)
        # The profile is created only after readiness, never contains production credentials.
        raise


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['start', 'cleanup'])
    p.add_argument('--run-id', required=True)
    p.add_argument('--profile', type=Path)
    args = p.parse_args(argv)
    def interrupted(signum, frame):
        raise CapabilityError('Disposable database operation interrupted')
    if args.action == 'start':
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
    try:
        if args.action == 'start':
            if args.profile is None:
                p.error('start requires --profile')
            start(args.run_id, args.profile)
            print('Disposable memory database ready (no host port published).')
        else:
            cleanup(args.run_id)
    except (CapabilityError, OSError, ValueError) as error:
        print(f'ERROR: {error}', file=__import__('sys').stderr)
        return 69
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
