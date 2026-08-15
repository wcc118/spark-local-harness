# ADR 0001: Codex governs local-first execution

## Status

Accepted for scaffold implementation.

## Decision

Codex owns architecture, escalation, final semantic review, and merge authority.
Local models perform bounded mapping, implementation, test design, failure
analysis, and adversarial scouting. OpenCode remains a pinned, replaceable
runtime adapter.

The harness will first add normalized health evidence and a compact Codex merge
gate. More autonomous routing is deferred until observed outcomes justify it.

## Reason

The preceding benchmark showed that local models can perform substantial work
but cannot reliably replace frontier semantic judgment. It also showed that
deterministic containment prevents bad local output from becoming durable
repository state.

The economic objective is high local autonomy with sparse, high-leverage
frontier intervention rather than frontier-parity on every task.

## Measurement

Track correctness, local autonomy, frontier-token share, Codex correction and
takeover rates, containment, infrastructure failures, total cost, and wall time.

## Rollback

The immutable `baseline-v2-extracted` tag and operational baseline profile remain
available until a final baseline-v2 versus candidate smoke test supports
promotion.

