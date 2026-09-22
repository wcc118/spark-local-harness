Synthetic qualification issue #186, wcc118/codex-local-harness. Correct only
qualification/convergence_fixture/interpolate.py and its tests. Harness code and
flight-loads are out of scope; you are a test subject, not a harness developer.

Contract: at least two finite time/value pairs, strictly increasing times; linear
interpolation in query order including endpoints. Queries at most 1e-12 outside
endpoints clamp; finite queries farther outside return None. Fewer than two points,
duplicate/decreasing times, non-finite point times/values AND non-finite queries
(NaN, +inf, -inf) must raise ValueError. Never mutate inputs. Standard library only.

Review correction: non-finite queries currently return None and the supplied test
expects None. Correct this mismatch and add regressions for NaN and both infinities
without weakening the other checks. Verify using python3 -m pytest -q
qualification/convergence_fixture. Commit the verified change on the current branch.
Do not push, use gh, merge, install packages or delegate. The deterministic harness
publishes and handles workflow state. Summarize observed verification briefly.
