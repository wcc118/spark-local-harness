# Native Qwen3.8 XHigh Partial Baseline

Issue: [#155](https://github.com/wcc118/codex-local-harness/issues/155)

Date: 2026-08-26. Runtime: Qwen Code 0.22.0 in native mode. Model:
`Qwen/Qwen3.8-27B-FP8` snapshot
`017b9c7af6b5689d5dd426a76e0bc077eb5ca20a` on the sequential Spark vLLM
endpoint.

The final condition explicitly passed these vLLM chat-template controls:

- `reasoning_effort: xhigh` (Qwen3.8's highest and default depth)
- `enable_thinking: true`
- `preserve_thinking: true`
- 4x the standard fixture timeout, recorded in attempt identity

These are reasoning-depth controls, not a fixed thinking-token budget. Raw
traces contain streamed thinking blocks. A standard-timeout run showed the
model reaching the correct intended edit immediately before termination,
confirming that the original bound clipped useful reasoning.

## Completed results

The operator deliberately stopped the run during the sixth case because the
latency result was already operationally decisive. This is a partial study,
not a 10-case or three-repetition result.

| Case | Correct clean exit | Wall time |
|---|---:|---:|
| refactor | yes | 97.5s |
| focused-fix | yes | 138.2s |
| UI | yes | 231.7s |
| test-authoring | yes | 393.3s |
| investigation | yes | 611.8s |

Aggregate: 5/5 correct clean exits, 1,472.5s wall time, 231.7s median,
19 tool calls, one tool error, and 739,307 runtime-reported tokens.

For the same five cases, native Qwen Code with Coder-Next averaged about 172
seconds per repetition in the prior three-run baseline. Qwen3.8 was therefore
approximately 8.5x slower in aggregate. Both models were correct on these five
cases in the available native Qwen Code samples.

## Interpretation

Qwen3.8 can produce Coder-Next-quality results on this partial corpus when its
xhigh reasoning loop is allowed to finish. Its primary observed limitation is
agentic latency, not lack of thinking or inability to solve these tasks. The
5/5 result does not establish reliability on the five unrun cases.

Do not promote Qwen3.8 as the default coder. Retain it only as an experimental
high-latency route if its different reasoning behavior is valuable enough to
justify roughly an order-of-magnitude slowdown.

Raw final attempts:
`~/.local/share/codex-local-harness/native-qwen38-xhigh4-20260826-r1/`.
Earlier implicit, standard-bound, and incompatible-wire attempts are discarded
and are not included in the aggregate.
