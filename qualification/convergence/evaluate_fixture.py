"""Host acceptance oracle, not included in the model's fixture checkout."""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from pathlib import Path


def evaluate(checkout: Path) -> dict:
    path = checkout / "qualification/convergence_fixture/interpolate.py"
    spec = importlib.util.spec_from_file_location("fixture_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    f = module.interpolate
    failures = []
    checks = 0
    def check(name, condition):
        nonlocal checks
        checks += 1
        if not condition:
            failures.append(name)
    for name, points, queries, expected in [
        ("midpoint", [(0., 0.), (1., 10.)], [.5], [5.]),
        ("endpoints", [(0., 2.), (1., 4.)], [0., 1.], [2., 4.]),
        ("fractional-endpoint", [(0., 0.), (.12, 12.)], [.1 + .02], [12.]),
        ("outside", [(0., 0.), (1., 10.)], [-.01, 1.01], [None, None]),
        ("order", [(0., 0.), (1., 10.), (2., 30.)], [1.5, .5], [20., 5.]),
        ("empty-queries", [(0., 0.), (1., 1.)], [], []),
    ]:
        original = list(points)
        try:
            check(name, f(points, queries) == expected)
            check(name + "-immutable", points == original)
        except Exception as exc:
            check(name + ":" + type(exc).__name__, False)
    for name, points, queries in [
        ("duplicate", [(0., 0.), (0., 1.)], [0.]),
        ("decreasing", [(1., 0.), (0., 1.)], [.5]),
        ("one-point", [(0., 0.)], [0.]),
        ("nonfinite-time", [(0., 0.), (math.inf, 1.)], [.5]),
        ("nonfinite-value", [(0., math.nan), (1., 1.)], [.5]),
        ("nonfinite-query", [(0., 0.), (1., 1.)], [math.nan]),
    ]:
        try:
            f(points, queries)
        except ValueError:
            check(name, True)
        except Exception:
            check(name + "-wrong-exception", False)
        else:
            check(name, False)
    return {"checks": checks, "failures": failures, "accepted": not failures}


if __name__ == "__main__":
    result = evaluate(Path(sys.argv[1]))
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["accepted"] else 1)
