# Final frozen benchmark

The frozen candidate passed all three seeded benchmark cases after bounded local
correction and Codex adjudication. The baseline completed one of three workflows;
the candidate completed three of three while using 3,611,342 local tokens versus
8,234,089 baseline core tokens, a 56.1% reduction. Measured worker wall time fell
from 2,573.836 seconds to 950.635 seconds, a 63.1% reduction.

This is not evidence that the local system is independently frontier-equivalent.
Codex found a material omission in every initial candidate result, and the first
complex reviewer approved a nonportable implementation. The demonstrated gain is
economic leverage: local workers performed most implementation and bounded review
work, isolated disposable checkouts prevented source contamination, and Codex
remained the merge authority that caught the final boundary defects.

## Decision

Promote with follow-ups. The containment and bounded-review architecture is a
clear improvement and the final products were correct, but local approval must not
become merge authority. The next diagnostic work should target reviewer recall,
GitHub post-push eventual consistency, and deterministic scratch/format checks.

## Measurement notes

- Both arms used the same seeded small, medium, and complex tasks on the same DGX
  Spark with Qwen3-Coder-Next-FP8. Candidate load time was excluded after warm-up.
- Baseline core totals omit one 511,866-token audit false-positive run; the
  inclusive baseline total is retained in `summary.json`.
- Frontier/Codex token telemetry is unavailable, so the value is reported as
  unsupported rather than zero. Local inference cost is reported only as zero
  marginal API cost; electricity and hardware amortization are not estimated.
- Raw model event logs stay in the operator-owned append-only run directory and
  are not committed because they include model reasoning. GitHub issues and PR
  comments retain the durable task, review, and publication evidence.
