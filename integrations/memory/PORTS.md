# Memory port registry

Project-local source of truth, following Finance and Flight-loads registration.
Register fixed host ports here before deployment; inspect reservations and listeners.

| Service | Host binding | Container port | Status |
|---|---|---|---|
| SurrealDB HTTP / MCP | 127.0.0.1:9600 | 8000 | Assigned |
| Surrealist graph browser | 127.0.0.1:9601 | 8080 | Assigned |
| Memory tools MCP | 127.0.0.1:9602 | n/a | Assigned; local agents only, no PC forward needed |
| PC SSH forwards | PC loopback 9600 and 9601 to the same host ports | n/a | Session-scoped; check PC availability |

## Registration evidence — 2026-10-23

`~/opencode/port/registry.yaml` is absent (the directory contains only `.gitkeep`).
Existing registries: `~/projects/aero/PORTS.md`, Aero dgxport configuration,
`~/projects/prod-ai/PORTS.md`, `~/projects/finance/PORTS.md`, and
`~/projects/panel_solver/PORTS.md` were inspected. They reserve blocks through
9500–9599, plus individual ports. No inspected reservation overlaps 9600/9601;
`ss -ltn` showed neither port listening before assignment.

Initial memory host bindings 8000 and 8080 were incorrect: prod-ai assigns 8000
to vLLM and reserves 8080. Those bindings are released by this correction.
Container-internal ports do not reserve host ports and remain unchanged.
No global registry or other project's allocations are modified.
Port 9602 was checked against these registries and active listeners before the
memory tools service was added for harness issue #214.

## PC access

Run on the PC, keeping the SSH session open:

```powershell
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:9600:127.0.0.1:9600 -L 127.0.0.1:9601:127.0.0.1:9601 connellwc83@10.0.0.98
```

Open http://127.0.0.1:9601 and connect to http://127.0.0.1:9600.
Reuse an existing matching tunnel; do not stop unrelated listeners.
PC forwarding is documented here, not claimed to be installed or verified remotely.
These host reservations persist even when memory services are stopped.
