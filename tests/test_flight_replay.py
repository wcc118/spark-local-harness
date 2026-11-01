"""Retain a failed improvement experiment without promoting its candidate."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1] / 'qualification' / 'flight-replay'
pytestmark = pytest.mark.skipif(
    any(importlib.util.find_spec(name) is None for name in ('numpy', 'scipy')),
    reason='flight replay requires the recorded numerical environment')


def run(script, *args):
    result = subprocess.run([sys.executable, str(script), *map(str, args)],
                            text=True, capture_output=True, timeout=60)
    return result.returncode, json.loads(result.stdout)


def test_frozen_baseline_and_candidate_development_results(tmp_path):
    with zipfile.ZipFile(ROOT / 'baseline.zip') as archive:
        baseline = tmp_path / 'baseline.py'
        baseline.write_bytes(archive.read('src/flightloads/aircraft_design_compare.py'))
    code, report = run(ROOT / 'replay.py', '--candidate', baseline)
    assert code == 1
    assert [r['status'] for r in report['checks']] == ['failed', 'failed']
    code, report = run(ROOT / 'replay.py')
    assert code == 0
    assert [r['status'] for r in report['checks']] == ['passed', 'passed']


def test_escape_is_executable_and_candidate_stays_rejected():
    code, report = run(ROOT / 'recurrence.py')
    assert code == 1
    assert report['detail'] == 'missing flight_condition claims root force'
    outcome = json.loads((ROOT / 'outcome.json').read_text())
    assert outcome['decision'] == 'reject-candidate-retain-replay'
    assert outcome['candidate_sha256'] == hashlib.sha256((ROOT / 'candidate.py').read_bytes()).hexdigest()


def test_fixture_tampering_is_execution_error(tmp_path):
    copy = tmp_path / 'replay'
    shutil.copytree(ROOT, copy)
    with (copy / 'baseline.zip').open('ab') as stream:
        stream.write(b'tampered')
    code, report = run(copy / 'replay.py')
    assert code == 2
    assert report['status'] == 'execution-error'


def test_broken_candidate_import_is_execution_error(tmp_path):
    broken = tmp_path / 'broken.py'
    broken.write_text('this is not valid Python !!!')
    code, report = run(ROOT / 'replay.py', '--candidate', broken)
    assert code == 2
    assert report['error'] == 'SyntaxError'
