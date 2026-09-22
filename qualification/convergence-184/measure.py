"""Record non-overlapping qualification intervals using scalar session counters."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from codex_local_harness.assessment_usage import counters, FIELDS
from codex_local_harness.supervision import append

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('phase', choices=['start', 'finish'])
parser.add_argument('interval')
args = parser.parse_args()
path = Path(__file__).with_name('frontier-intervals.jsonl')
prior = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
if args.phase == 'start':
    assert not prior or prior[-1]['phase'] == 'finish', 'Overlapping interval'
    assert not any(e['interval'] == args.interval for e in prior), 'Duplicate interval'
else:
    assert prior and prior[-1]['phase'] == 'start' and prior[-1]['interval'] == args.interval
value = {'phase': args.phase, 'interval': args.interval, 'captured_at': time.time(),
         'session_id': os.environ['CODEX_THREAD_ID'],
         'scope': 'qualification-only interval in existing conversation; not a fresh session'}
try:
    value.update(counters(Path.home()/'.codex/sessions', value['session_id']))
except (OSError, ValueError) as exc:
    value['unavailable'] = type(exc).__name__
if args.phase == 'finish':
    start = prior[-1]
    delta = ({k: value['counters'][k]-start['counters'][k] for k in FIELDS}
             if 'counters' in value and 'counters' in start else None)
    value['delta'] = delta if delta is not None and min(delta.values()) >= 0 else None
append(path, value)
print(json.dumps(value))
