# Agent-directed shared memory

Issue #214 adds optional memory tools to the installed Qwen Code coder and OpenCode
reviewer paths. It does not choose searches or inject retrieved answers into packets.
Agents choose retrieval, assess evidence and applicability, and record lessons from
investigation, review and feedback. Codex can contribute and reconcile too.

The separate local memory deployment lives in `~/projects/memory`. Service source
is reviewed in `integrations/memory/`; the deployment directory retains its own local
Git history. Database storage and all credentials remain outside this repository.
The server uses the existing SurrealDB instance and the MCP Python SDK. See the
integration README for installation, ports, authentication, tests and rollback.

## Worker configuration

An optional owner-only `~/.config/codex-local/memory.json` holds `enabled`, a loopback
`url` ending `/mcp`, and an `issuer_token`. Override the file with
`CODEX_LOCAL_MEMORY_CONFIG`. The issuer credential stays on the host. It can mint
only coder/reviewer/helper identities, never a frontier identity.

Before inference the launcher requests a seven-day credential bound to role, run,
repository and issue, then writes it into that run's private runtime configuration.
Both OpenCode (`mcp.memory`) and Qwen Code (`mcpServers.memory`) receive the endpoint.
The caller's role is enforced by the service, not by a model-supplied argument.
Credentials authorize a role and identify the run; they do not establish the truth
of the author's source/evidence claims. Runtime homes are private and contain bearer
credentials: do not publish them. Deleting a token's file does not revoke a token;
rotating the service signing key revokes all worker tokens.

Coder/reviewer tools: search_memory, get_memory, record_memory, correct_memory.
Helper tools: search_memory, get_memory. Codex additionally has resolve_memory.
No routine client receives arbitrary SQL or a deletion tool from this service.
The user/admin can still manage the underlying database directly. These MCP boundaries
are not a sandbox against the workstation owner or an unrestricted host shell.

The reviewer tool-scope audit explicitly recognizes the four worker memory operations:
source paths in a finding are data, not filesystem access. Repository permissions,
publication boundaries, exact-head review and the result schema remain unchanged.
Other memory tool names, including reconciliation, remain prohibited in worker reviews.

Missing configuration or an unavailable issuer produces no memory credential and
inference continues. Inherited interactive memory configs/host instruction paths are
removed before configuration so missing host files do not break isolated workers.
Tool failures during a run are evidence to report, not a reason to abandon the task.

## Learning and review

End-of-run summaries can mention useful retrieved, created or challenged memory IDs.
No mandatory new JSON field, minimum number of searches, approval gate or invented
lesson is imposed. A reviewer may write a memory while code remains read-only.
Use exclude_issue="OWNER/REPO#N" to avoid current-issue claims during independent
review. Full-text and literal matching are available; no semantic embeddings or
weight updates are performed.

Record writes are idempotent within an authenticated actor/run using request_id.
Corrections add a linked claim and mark an active original disputed; historical
text stays unchanged. Searches include active/disputed claims by default. Codex can
supersede an old claim with an active or disputed replacement and rationale. Inspection retains
relationships and audit events. Permanent deletion is a separate owner-directed
administrative operation, including consideration of backup copies.

## Verification and rollback

`PYTHONPATH=src python3 -m pytest -q tests/test_memory.py tests/test_review.py`
verifies configuration, optional behavior, and review audit boundaries. The service's
integration suite creates/removes only an isolated temporary database. Test both
runtime discovery paths with their generated configs before promotion; do not claim
model learning or effectiveness from tool-discovery checks alone.

Set `enabled: false` in the private memory config to disable future worker connections.
Use the install-harness receipt's rollback command to restore the previous installed
launcher. Restore only the memory entries in interactive client configs; do not clobber
unrelated later changes. Rollback leaves knowledge data intact. Stop the memory-tools
user service if needed; database and graph browser are separate services.
