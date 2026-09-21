# OpenCode versus Qwen Code on Spark

Issue: [#151](https://github.com/wcc118/codex-local-harness/issues/151)

Run date: 2026-08-25

Hardware/server: one sequential Spark vLLM endpoint, vLLM `0.19.2rc1.dev206+g95995bbef.d20260425`

## Frozen conditions

| ID | Runtime | Model |
|---|---|---|
| A | OpenCode 1.18.21 | `Qwen/Qwen3.8-27B-FP8` |
| B | Qwen Code 0.22.0 | `Qwen/Qwen3.8-27B-FP8` |
| C | OpenCode 1.18.21 | `Qwen/Qwen3-Coder-Next-FP8` |
| D | Qwen Code 0.22.0 | `Qwen/Qwen3-Coder-Next-FP8` |

Each condition ran the same ten-case `paired-model-se-v1` corpus sequentially. Each attempt received a fresh copied workspace, an isolated runtime home, the shared `runtime-v1` boundary policy, and the manifest's 180–240 second timeout. The hidden oracle stayed outside the workspace. A trace audit found no references to the oracle, prior attempt results, or the discarded pre-isolation run.

## Results

| Condition | Oracle passes | Clean passes | Timeouts | Wall time | Tool calls | Reported tokens |
|---|---:|---:|---:|---:|---:|---:|
| A | 3/10 | 0/10 | 10 | 1,983.7s | 48 | 202,946 |
| B | 6/10 | 3/10 | 7 | 1,848.4s | 29 | 505,988 |
| C | 9/10 | 9/10 | 0 | 493.0s | 103 | 880,703 |
| D | 9/10 | 9/10 | 0 | 229.3s | 27 | 981,301 |

“Oracle pass” means the resulting workspace passed after the process ended, including timeout termination. “Clean pass” additionally requires a normal runtime exit. Under Qwen Code, Qwen3.8 cleanly passed focused-fix, refactor, and UI; diagnosis, hygiene, and concurrency reached correct workspace state but failed to stop before timeout. Under OpenCode, all Qwen3.8 attempts timed out, although focused-fix, refactor, and cleanup left oracle-passing workspaces.

## Interpretation

Qwen Code materially improves Qwen3.8 over OpenCode, but does not make it production-reliable: only 3/10 attempts both passed and terminated normally. Coder-Next tied at 9/10 clean passes under both runtimes, with different misses (OpenCode investigation; Qwen Code cleanup). Qwen Code reduced Coder-Next aggregate wall time by 53.5% and tool calls by 73.8% relative to OpenCode.

Reported tokens are not directly comparable between runtimes. Qwen Code includes a large native system/tool scaffold and reports cumulative request usage; OpenCode emits a different step-oriented accounting shape. Endpoint-side normalized accounting was unavailable. On Coder-Next, Qwen Code reported 11.4% more tokens despite fewer tools and lower wall time.

## Decision

Keep Coder-Next as the default model. Qwen Code is a promising alternative worker runtime for Coder-Next and a useful experimental runtime for Qwen3.8, but this single-run corpus is not enough to promote either route. Run repeated trials, strengthen the cleanup and investigation fixtures, and add endpoint-side token accounting before a production runtime change.

## Evidence identity

- Source commit: `1157a98602e00d35a714ada5acf9a44688adeb7f`
- Qwen3.8 snapshot: `017b9c7af6b5689d5dd426a76e0bc077eb5ca20a`
- Coder-Next snapshot: `da6e2ed27304dd39abadd9c82ef50e8de67bdd4c`
- Manifest SHA-256: `cce6769c9917cf2cec3a30c020c8f19c9eb074d43cde3ff5957cb08b9852a0b3`
- Oracle SHA-256: `5ac4c311d455f3d592d0556bd5e69389bf4808003e148ecda2c1e0f410a7d864`
- Runtime runner SHA-256: `fe82223acd4cc47af98a5e36ed3d1612aa82977f3cd55391faa389c92d2018e0`
- Runtime policy SHA-256: `c142a973aaf363a8b5ccc3d32f573922cd4cf6bed2f0f3eb9d21b1155a15ec4c`
- OpenCode config SHA-256: `455b61e90575975485c4f4d78f904203cd4ac3aa19dc17989563a119a74283c7`
- Qwen Code lock SHA-256: `4ec7633db5619515837a66071634d71bd160852e6bf63d5f0e4c3115ecb0537f`
- Raw attempts: `~/.local/share/codex-local-harness/runtime-abcd-isolated-20260825/`

An earlier run at `runtime-abcd-20260825/` is explicitly discarded: trace audit showed an OpenCode case reading sibling attempt results. Its numbers are not used above.
