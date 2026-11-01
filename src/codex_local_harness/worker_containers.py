"""Conservative cleanup of labelled worker containers, never model services."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from .supervision import append, process_start

PREFIX = 'io.codex.worker.'


def docker(*args):
    result = subprocess.run(['docker', *args], text=True, capture_output=True, timeout=10 if args[0] == 'stop' else 5)
    if result.returncode:
        raise RuntimeError('Docker operation failed: ' + args[0])
    return result.stdout


def candidate(item: dict, now: float, max_age: float, uid: int, probe=process_start) -> dict | None:
    """Return only positively identified stale workers. Unknown ownership skips."""
    config = item.get('Config') or {}
    if not isinstance(config, dict):
        return None
    labels = config.get('Labels') or {}
    if not isinstance(labels, dict):
        return None
    identifier = item.get('Id', '')
    if (labels.get(PREFIX+'managed') != '1' or labels.get(PREFIX+'uid') != str(uid)
            or not re.fullmatch(r'[a-f0-9]{64}', identifier)):
        return None
    run = labels.get(PREFIX+'run', '')
    role = labels.get(PREFIX+'role')
    if (not re.fullmatch(r'[A-Za-z0-9._-]+', run) or role not in {'coder','reviewer','helper','investigator'}
            or item.get('Name') != '/codex-worker-'+run):
        return None
    try:
        started = float(labels[PREFIX+'started'])
        pid = int(labels[PREFIX+'pid'])
        ticks = labels[PREFIX+'start_ticks']
        if pid <= 1 or not isinstance(ticks,str) or not ticks.isdigit() or not 0 <= started <= now:
            return None
        age = now-started
        if age < max_age:
            return None
        try:
            if probe(pid) == ticks:
                return None  # Still belongs to a live wrapper, even if very old.
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            return None  # Cannot establish that the owner is gone.
    except (KeyError, TypeError, ValueError):
        return None
    return {'container_id':identifier,'run_id':run,'role':role,'age_seconds':int(age)}


def save_snapshot(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with tempfile.NamedTemporaryFile('w',dir=path.parent,delete=False) as f:
        json.dump(value,f,sort_keys=True)
        temporary=Path(f.name)
    temporary.replace(path)


def reap(health: Path, max_age: float = 1860, *, execute=docker, probe=process_start, now=None) -> dict:
    if not math.isfinite(max_age) or max_age <= 0:
        raise ValueError('max age must be positive')
    now=time.time() if now is None else now
    result={'observed_at':now,'status':'ok','stale':0,'stopped':0,'errors':0}
    try:
        ids=execute('ps','-q','--no-trunc','--filter','label='+PREFIX+'managed=1',
                    '--filter','label='+PREFIX+'uid='+str(os.getuid())).split()
        deadline = time.monotonic() + 10
        for identifier in ids[:128]:
            if time.monotonic() >= deadline:
                result['status'] = 'truncated'
                break
            if not re.fullmatch(r'[a-f0-9]{64}',identifier):
                continue
            items=json.loads(execute('inspect',identifier))
            if not isinstance(items,list) or len(items)!=1 or not isinstance(items[0],dict) or items[0].get('Id')!=identifier:
                continue
            stale=candidate(items[0],now,max_age,os.getuid(),probe)
            if stale is None:
                continue
            result['stale']+=1
            event={**stale,'observed_at':now,'action':'stop-failed'}
            try:
                execute('stop','--time','5',identifier)
                event['action']='stopped'
                result['stopped']+=1
            except (OSError,RuntimeError,subprocess.TimeoutExpired):
                result['errors']+=1
            append(health/'worker-containers.jsonl',event)
        if len(ids)>128:
            result['status']='truncated'
    except (OSError,RuntimeError,ValueError,TypeError,subprocess.TimeoutExpired):
        result['status']='unavailable'
        result['errors']+=1
    save_snapshot(health/'worker-containers.json',result)
    return result


def snapshot(health: Path) -> dict:
    try:
        value=json.loads((health/'worker-containers.json').read_text())
        if not isinstance(value,dict):
            raise ValueError('not object')
        return {k:value[k] for k in ('observed_at','status','stale','stopped','errors') if k in value}
    except (OSError,ValueError):
        return {'status':'unknown'}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--health',type=Path,required=True)
    p.add_argument('--max-age',type=float,default=1860)
    args=p.parse_args(argv)
    try:
        print(json.dumps(reap(args.health,args.max_age),sort_keys=True))
    except ValueError as exc:
        p.error(str(exc))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
