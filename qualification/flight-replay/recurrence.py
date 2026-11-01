"""Previously private escape, published AFTER the first evaluation.

This is now development/regression evidence, never a fresh holdout again.
"""
import argparse
import json
from pathlib import Path
import tempfile
import replay


def check(module):
    spec = replay.specification()
    spec['configurations'][1]['definition'].pop('flight_condition')
    rows = module.compare_designs(spec)['rows']
    assert len(rows) == 2
    assert rows[0]['metrics']['root_Fz_lbf'] is not None
    assert rows[1]['metrics']['root_Fz_lbf'] is None, 'missing flight_condition claims root force'
    assert rows[1]['metrics']['root_Mx_in_lbf'] is None
    assert rows[1]['delta_from_baseline']['root_Fz_lbf'] is None
    assert rows[1]['load_reasons']


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--candidate', type=Path, default=Path(__file__).with_name('candidate.py'))
    args = p.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            module = replay.load(args.candidate.resolve(), Path(tmp))
        except Exception as error:
            print(json.dumps({'status':'execution-error','error':type(error).__name__}))
            return 2
        try:
            check(module)
        except Exception as error:
            print(json.dumps({'status':'failed','error':type(error).__name__,'detail':str(error)}))
            return 1
        print(json.dumps({'status':'passed'}))
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
