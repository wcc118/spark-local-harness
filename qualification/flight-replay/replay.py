"""Run fixed behavioral development checks against the real supplied module."""
from __future__ import annotations
import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent


def load(candidate: Path, destination: Path):
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    archive = ROOT / 'baseline.zip'
    if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest['fixture_sha256']:
        raise ValueError('baseline fixture hash mismatch')
    with zipfile.ZipFile(archive) as source:
        for name in source.namelist():
            target = (destination / name).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError('fixture path escapes extraction directory')
        source.extractall(destination)
    sys.path.insert(0, str(destination / 'src'))
    import flightloads
    spec = importlib.util.spec_from_file_location('flightloads.aircraft_design_compare', candidate)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def specification():
    from flightloads.aircraft_workspace import stable_example_definition
    definition = stable_example_definition()
    return {'schema': 'dive-load-comparison-v1',
            'configurations': [{'name': 'reference', 'definition': definition},
                               {'name': 'alternate', 'definition': deepcopy(definition)}],
            'cases': [{'name': 'screen', 'speed_ft_s': 100, 'load_factor': 1}]}


def input_unchanged(module):
    spec = specification()
    before = json.dumps(spec, sort_keys=True)
    definition = spec['configurations'][0]['definition']
    components = definition['components']
    module.compare_designs(spec)
    assert json.dumps(spec, sort_keys=True) == before, 'caller serialization changed'
    assert spec['configurations'][0]['definition'] is definition, 'definition object replaced'
    assert definition['components'] is components, 'component list replaced'


def invalid_definition_isolated(module):
    spec = specification()
    spec['configurations'][1]['definition']['flight_condition'] = None
    report = module.compare_designs(spec)
    assert len(report['rows']) == 2, 'one row per configuration/case required'
    valid, invalid = report['rows']
    assert valid['metrics']['root_Fz_lbf'] is not None, 'valid row lost'
    assert invalid['metrics']['root_Fz_lbf'] is None, 'invalid row claims root force'
    assert invalid['metrics']['root_Mx_in_lbf'] is None, 'invalid row claims root moment'
    assert invalid['load_reasons'], 'unavailable row needs explanation'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', type=Path, default=ROOT / 'candidate.py')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='flight-replay-') as directory:
        try:
            module = load(args.candidate.resolve(), Path(directory))
        except Exception as error:
            print(json.dumps({'status': 'execution-error', 'error': type(error).__name__}))
            return 2
        rows = []
        for check in (input_unchanged, invalid_definition_isolated):
            try:
                check(module)
                rows.append({'check': check.__name__, 'status': 'passed'})
            except Exception as error:
                rows.append({'check': check.__name__, 'status': 'failed',
                             'error': type(error).__name__, 'detail': str(error)})
        passed = all(r['status'] == 'passed' for r in rows)
        print(json.dumps({'status': 'passed' if passed else 'failed', 'checks': rows}))
        return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
