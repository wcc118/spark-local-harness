# Agent-directed shared memory

Issue #214 adds optional memory tools to the installed Qwen Code coder and OpenCode
reviewer paths. It does not choose searches or inject retrieved answers into packets.
Agents choose retrieval, assess evidence and applicability, and record lessons from
investigation, review and feedback. Codex can contribute and reconcile too.

Issue #220 adds discover_topics and enrich_memory tools for optional agent-directed
discovery and additive metadata enrichment. Discover_topics lets agents identify
existing topic names with optional case-insensitive substring matching. Enrich_memory
adds topic edges and audit events to existing memories without changing text, source
or status. Both tools are read/write appropriate based on role and contribute to
structured metadata without affecting claim integrity.

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

Coder/reviewer tools: search_memory, get_memory, record_memory, correct_memory,
record_memory_use, discover_topics, enrich_memory. Helper tools: search_memory,
get_memory, discover_topics. Codex additionally has resolve_memory. No routine
client receives arbitrary SQL or a deletion tool from this service. The user/admin
can still manage the underlying database directly. These MCP boundaries are not
a sandbox against the workstation owner or an unrestricted host shell.

The reviewer tool-scope audit explicitly recognizes the seven worker memory operations:
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

Discover_topics allows optional agent-directed discovery of existing topic names
with case-insensitive substring matching. Use this to identify existing topics
before enrichment or to audit the topic namespace. Limit validation constrains
result set size; no quota or mandatory report is imposed.

Enrich_memory adds topic edges and audit events with attributed source/explanation.
Preserves original text, source, status and project. No removal, text rewrite,
alias normalization, semantic link inference or schema migration. Similar labels
need not mean the same thing; inspect applicability before reuse. Idempotent
replay with request_id prevents duplicate concerns edges or events.

Record writes are idempotent within an authenticated actor/run using request_id.
Corrections add a linked claim and mark an active original disputed; historical
text stays unchanged. Searches include active/disputed claims by default. Codex can
supersede an old claim with an active or disputed replacement and rationale. Inspection retains
relationships and audit events. Permanent deletion is a separate owner-directed
administrative operation, including consideration of backup copies.

Use `record_memory_use` to report actual lesson use as helpful/unhelpful/misleading/
uncertain. This append-only attributed report does not change the lesson text or status.
Distinguish between searching/reading (assessment) and applying a lesson (use report).
Report honest positive, negative, or uncertain outcomes with source references and
applicability limitations. No per-task use report is required; agents exercise
discretion based on the task context. There is no automatic popularity/confidence
score, and no inference that fewer failures are already measured. Memory unavailability
does not block your investigation or implementation.

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
