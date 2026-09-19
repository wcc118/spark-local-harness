Independent review of synthetic fixture PR #183, issue #182,
wcc118/codex-local-harness. Review only qualification/convergence_fixture.
This is the clarified-contract sensitivity trial following the original paired
trial; it is not an identical-task baseline comparison.

Acceptance: interpolate(points, queries) requires at least two finite time/value
pairs, strictly increasing times. Return linear interpolation in query order,
including endpoints. Queries at most 1e-12 outside an endpoint are clamped to that
endpoint; finite queries farther outside return None. Reject duplicate/decreasing
times, non-finite point times/values, and fewer than two points with ValueError.
Explicit clarification: non-finite query times (NaN, +inf, -inf) must also raise
ValueError. Do not mutate inputs; use only the standard library.

The coder reported 26 passing tests. Independently run those tests. Verify the
clarified invalid-query requirement with a focused diagnostic test written in the
harness-provided scratch directory, using the read-only submitted implementation.
Do not edit submitted code or tests. When the evidence supports a verdict, return
the complete harness-provided result schema with the concrete finding and observed
test evidence. Harness code is out of scope. Do not publish or delegate.
