Synthetic qualification issue #186, wcc118/codex-local-harness. Review only
qualification/convergence_fixture/interpolate.py and its tests at the supplied
immutable PR head. Harness implementation is out of scope.

Contract: at least two finite time/value pairs, strictly increasing times; linear
interpolation in query order including endpoints. Queries at most 1e-12 outside
endpoints clamp; finite queries farther outside return None. Fewer than two points,
duplicate/decreasing times, non-finite point times/values AND non-finite queries
(NaN, +inf, -inf) must raise ValueError. Never mutate inputs. Standard library only.

Review this contract, especially whether existing tests actually enforce rejection
of all three non-finite queries. Follow the harness's tool/evidence boundary.
Do not assume a passing supplied suite proves acceptance. Use python3 -m pytest -q
qualification/convergence_fixture when tools are available; put any diagnostics
in the supplied scratch directory. Once a verdict is supported, return the complete
single-line REVIEW_RESULT_JSON. Keep the narrative brief. Do not modify submitted
files, change acceptance, publish, install packages, or delegate.
