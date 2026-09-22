Independent review of synthetic fixture PR #183 for issue #182 in
wcc118/codex-local-harness. Review only qualification/convergence_fixture; do not
review or modify harness code. This is a local-model test subject.

Acceptance: interpolate(points, queries) accepts at least two finite (time, value)
pairs with strictly increasing times and finite query times. Return linearly
interpolated values in query order, including endpoints. Queries no more than
1e-12 beyond an endpoint from floating-point rounding are treated as that endpoint;
queries farther outside return None. Reject duplicate/decreasing times, non-finite
values and fewer than two points with ValueError. Do not mutate inputs. No third-party
dependencies. Investigate concrete contract failures; no style or visual work.

Reported evidence: the local implementation agent reported 26 passing fixture tests.
This is task-reported evidence, not proof of acceptance and not reviewer-executed.
The required command is python3 -m pytest -q qualification/convergence_fixture.
Follow the harness-provided evidence/tool boundary and return its complete schema.
