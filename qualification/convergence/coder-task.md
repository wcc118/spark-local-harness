Issue #182, wcc118/codex-local-harness: synthetic qualification task only.

Implement qualification/convergence_fixture/interpolate.py and add focused tests
in that directory. Do not inspect or modify harness implementation or other
qualification cases. This is a local-model test subject, not harness development.

interpolate(points, queries) accepts at least two finite (time, value) pairs with
strictly increasing times and finite query times. Return linear interpolated
values in query order. Include domain endpoints. A query at most 1e-12 beyond an
endpoint from floating-point rounding is treated as that endpoint. Queries farther
outside return None. Reject duplicate/decreasing times, non-finite values and fewer
than two points with ValueError. Do not mutate inputs. No third-party dependencies.

Run: python3 -m pytest -q qualification/convergence_fixture
Additional verification is welcome within this directory. Commit the verified
change on the current issue branch. Do not push, use gh, merge, install packages,
or delegate. The deterministic harness handles publication. Stop when the contract
is supported, or explain a specific blocker if unable to identify a productive
next experiment. No visual refinement or unrelated cleanup is requested.
