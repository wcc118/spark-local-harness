"""Opt-in admission for native LM Studio workers; never loads a model."""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

from .lane import check_lane


def admit(config, environment, check=check_lane):
    tier = environment.get('CODEX_LOCAL_TIER', 'managed-3090')
    route = config.get('routes', {}).get('coder/' + tier, {})
    model = route.get('model')
    if route.get('runtime') != 'qwen-code' or route.get('mode') != 'native' or not isinstance(model, str) or not model.startswith('lmstudio/'):
        raise ValueError('managed lane requires an explicit native qwen-code lmstudio coder route')
    override = environment.get('CODEX_LOCAL_MODEL')
    if override and override != model:
        raise ValueError('model override conflicts with managed lane route')
    base_url = environment.get('LMSTUDIO_BASE_URL', 'http://10.0.0.92:1234/v1').rstrip('/')
    status = check(base_url, model.removeprefix('lmstudio/'), min_context=65536,
                   timeout=8.0, token=environment.get('LMSTUDIO_API_TOKEN'))
    record = status.as_dict()
    # Network error details are not retained: URLs may contain credentials.
    record.pop('detail', None)
    return tier, model, record


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        print('usage: python -m codex_local_harness.managed_lane REPOSITORY TASK...', file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[2]
    state = Path.home() / '.local/share/codex-local-harness'
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    config_path = Path(os.environ.get('CODEX_LOCAL_WORKER_CONFIG', str(Path.home() / '.config/codex-local/workers.json')))
    try:
        config = json.loads(config_path.read_text())
        fd = os.open(state / 'lane-3090.lock', os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            record = {'status': 'lease-busy', 'checked_at': datetime.now(timezone.utc).isoformat()}
            tier = model = None
        else:
            tier, model, record = admit(config, os.environ)
        record['repository'] = os.environ.get('CODEX_LOCAL_REPOSITORY')
        record['issue'] = os.environ.get('CODEX_LOCAL_ISSUE')
        record['disposition'] = 'admitted' if record['status'] == 'ok' else 'skipped'
        ledger = os.open(state / 'lane-admissions.jsonl', os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        with os.fdopen(ledger, 'w') as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.write(json.dumps(record, sort_keys=True) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        print(json.dumps(record, sort_keys=True), flush=True)
        if record['disposition'] == 'skipped' or os.environ.get('CODEX_LOCAL_DRY_RUN') == '1':
            os.close(fd)
            return 0
        env = dict(os.environ, CODEX_LOCAL_TIER=tier, CODEX_LOCAL_MODEL=model,
                   CODEX_LOCAL_MODEL_SWITCH='0', CODEX_LOCAL_TOOL_CALL_BUDGET='80',
                   CODEX_LOCAL_TIMEOUT_SECONDS='900', CODEX_LOCAL_RECORD_RUN='1',
                   CODEX_LOCAL_LANE_CONTEXT=str(record['loaded_context']))
        # Keep the advisory lease for the entire managed execution, including cleanup.
        os.set_inheritable(fd, True)
        os.execve(root / 'bin/codex-local-worker-candidate',
                  ['codex-local-worker-candidate', 'coder', *args], env)
    except (OSError, ValueError, TypeError) as exc:
        print('managed lane admission failed: ' + type(exc).__name__, file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
