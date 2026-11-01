"""Public regression controls for the retained local-authored playback check."""
import json
from pathlib import Path
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1] / 'qualification/steward-retry'


@pytest.mark.parametrize('snapshot,expected', [('raw', 1), ('fix', 0)])
def test_retained_check_distinguishes_public_snapshots(snapshot, expected):
    run = subprocess.run(['node', str(ROOT / 'check.mjs'), str(ROOT / 'input' / snapshot)],
        capture_output=True, text=True, timeout=20)
    assert run.returncode == expected
    assert json.loads(run.stdout)['ok'] is (expected == 0)


def test_structural_failure_is_not_counted_as_behavioral_detection(tmp_path):
    target = tmp_path / 'src/flightloads/static/workbench.js'
    target.parent.mkdir(parents=True)
    target.write_text('const = ;')
    run = subprocess.run(['node', str(ROOT / 'check.mjs'), str(tmp_path)],
        capture_output=True, text=True, timeout=20)
    assert run.returncode == 2
    assert json.loads(run.stdout)['error']
