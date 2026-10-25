# Local shared memory

SurrealDB 3.3.4 stores knowledge across projects. A local Surrealist browser displays
records and graph relationships. Both Docker images are pinned by digest.

- Browser: http://127.0.0.1:9601
- Database: http://127.0.0.1:9600
- Memory tools MCP: http://127.0.0.1:9602/mcp
- Database-native MCP: http://127.0.0.1:9600/mcp (not registered with agents)
- Namespace: `personal`; database: `knowledge`
- Tracking: https://github.com/wcc118/codex-local-harness/issues/213

## Start and stop

Requires Docker Compose and Python 3. Run from this directory:

```sh
bash setup.sh
docker compose ps
docker compose stop
docker compose start
```

The services restart with Docker unless explicitly stopped. Data is stored in
`data/knowledge`, not inside the disposable container. `setup.sh` creates credentials
only when absent and bootstraps an additive schema. Do not delete `.env` or `.local`
on an existing installation: changing those files does not rotate database passwords.

## Browse the graph

Open http://127.0.0.1:9601 and add a connection to `http://127.0.0.1:9600`.
Choose **Database** authentication, namespace `personal`, database `knowledge`.
Get the writer login locally (this command intentionally displays the password):

```sh
python3 memoryctl.py connection --role memory_writer
```

Use `memory_reader` instead for a read-only browser. Root administration is available
with `--role admin`; agents do not use root credentials.

Run the queries in `examples.surql` and select the graph result view. The initial
`memory:purpose` record connects to `project:memory` and `topic:surrealdb`.
SurrealDB Studio succeeds Surrealist; this installation uses the available official
Surrealist container to serve the entire browser locally on this ARM machine.
No cloud account is required for the local database connection.

## Work with knowledge

`memory` holds text, source, topics, originating project, kind, status and creation time.
`project` and `topic` hold graph nodes. `discovered_in`, `concerns` and `supersedes`
hold relationships. Additional fields are allowed for experimentation.

Read from the terminal:

```sh
python3 memoryctl.py query < examples.surql
```

For writes, add `--role memory_writer`. Sources and conditions should accompany
findings. Search is stemmed full text (English snowball) over each memory's text,
conditions and topic names, held in the derived `search_text` field. A literal substring
fallback keeps exact identifiers matchable. Multi-word queries match any term and rank
by BM25. The original `memory_search` index remains defined but unused by the service.
Semantic embeddings and automatic extraction are not enabled. See `MEMORY.md` for shared
usage conventions.

## Agent connections installed on this workstation

- `~/.codex/config.toml`: narrow memory tools with the Codex/frontier identity;
  `client_headers.py` supplies the private bearer credential. Codex can record,
  propose corrections, reconcile and enrich. Discover_topics is also available.
  `enabled_tools` must list every tool; it listed only five until issue #242. Codex
  0.164 defers MCP tools behind a metadata catalog, so a session may report "no memory
  tools" until it searches that catalog. A fresh session found
  `mcp__memory__record_memory_use` there after the allowlist fix.
- Claude Code (user scope, `~/.claude.json`): all tools with its own `claude`
  frontier identity, registered via `headersHelper` running
  `client_headers.py claude`. Authorship is recorded as `claude`, not `codex`.
- Interactive OpenCode uses a contributor identity. Headless workers do not inherit
  interactive MCP servers: issue #214 explicitly provisions per-run credentials into
  Qwen Code and OpenCode configurations, permitting coder/reviewer contributions.
- The optional host-only `~/.config/codex-local/memory.json` contains the issuer
  credential. It can mint only coder/reviewer/helper tokens. Tokens are valid for
  seven days and bind role, run, repository and issue; helpers remain read-only.
- Reviewer code permissions stay read-only. Memory source references are data, not
  permission to access other repositories. Workers choose their own searches.
- Previous documentation claiming workers automatically inherited the interactive
  reader MCP was incorrect: the installed launcher disabled inherited MCP servers.

Restart clients or begin a new session to load changed MCP configuration. The current
conversation may not dynamically acquire new tools. The installed OpenCode CLI is
1.18.21; a separate OpenCode 2 installation may use a different configuration and
needs its own registration. No model routing or authentication to model providers
was changed.

This makes tools available and supplies usage instructions; it does not train models
or automatically inject every memory into every prompt. Both storage and search are
local. When a cloud-hosted agent retrieves a memory, that returned text enters its
model context; use the local model runtime for inference that must remain local.

## Backups and recovery

```sh
python3 memoryctl.py backup --verify
python3 memoryctl.py verify-backup [backups/NAME.surql]   # default: newest
systemctl --user list-timers memory-backup.timer
```

This exports `personal/knowledge` into a timestamped `backups/*.surql` file with
owner-only permissions, plus a `.counts.json` table-count snapshot. The snapshot is
re-taken if writes land during the export. `--verify` and `verify-backup` import the
export into a temporary `restore_check_*` database, compare counts, record
`.verify.json`, and always remove the temporary database. Backups can contain
database user definitions/password hashes; keep them private.

The enabled user timer `memory-backup.timer` (template in `deployment/`) runs a
verified export daily around 03:30 local time; `Persistent=true` catches up after
downtime. It is on-machine only, not an off-machine backup, and old exports are never
deleted automatically. Pruning is an owner decision. Check failures with
`journalctl --user -u memory-backup.service`.
Preserve `.env` and `.local/accounts.json` separately in secure storage. Root users are
outside the database export. For recovery, import an export into a fresh instance
using SurrealDB's import command; do not overwrite a live database casually.

Before upgrades, back up and review release migration notes. Do not copy a running
RocksDB directory as a substitute for an export.

See `PORTS.md` for port reservations and the PC SSH tunnel command.

## Configuration and rollback

Ports bind only to 127.0.0.1. If your browser is on a different computer, use an SSH
tunnel forwarding ports 9601 and 9600 instead of changing the bindings by default.
Passwords live in ignored owner-only files. Do not commit `.env`, `.local`, `data`,
or `backups`, and do not paste `docker compose config` output (it expands credentials).

Original client configuration snapshots are in `.local/config-backups/`.
To remove integration, remove only the `memory` MCP entries, the MEMORY.md instruction
reference and the appended shared-knowledge section. Avoid restoring an old entire
config over later unrelated edits. `docker compose down` removes the services but
leaves bind-mounted data intact. This directory is a local Git repository with no
remote configured; the tracking issue records the installation outcome.

## Memory tools service

`memory_service.py` runs as the enabled user unit `memory-tools.service` on registered
port 9602. User lingering is enabled on this workstation, so it starts at boot and
restarts on process failure. Database outages return tool errors; the model should
continue its engineering task. The service does not restart the database on failure.

```sh
systemctl --user status memory-tools.service
systemctl --user restart memory-tools.service
curl http://127.0.0.1:9602/health
MEMORY_INTEGRATION=1 .venv/bin/python -m unittest -q test_memory_service
```

The test suite creates/removes only a uniquely named temporary database. For a fresh
service environment, create `.venv`, install `requirements.txt`, run `setup.sh`, then
start `memory_service.py` (first start creates private `.local/service-auth.json`).
The workstation-specific unit template is in `deployment/`. Adjust paths if moving.

Service/client secrets are owner-only files in `.local`. Do not copy these into
worker packets or logs. Rotate the signing key to revoke all run credentials; rotate
individual client/issuer tokens to revoke those identities and update their consumers.
The service rereads `.local/service-auth.json` when its modification time changes, so
adding, rotating or revoking clients needs no restart. An unreadable or invalid file is
ignored and the last valid configuration stays in force (the journal logs which happened).
Client roles: `frontier` (codex, claude), `coder` (opencode); worker roles are issued
per run.
The service protects its MCP surface, not an unrestricted workstation owner's shell.
Direct SQL administration and the human graph-browser login remain separate.

`record_memory` and `correct_memory` create immutable-text records with automatic
project/topic edges and authenticated authorship. `challenges` links proposed
corrections. `memory_event` records lifecycle actions. Writes use request_id for
retry deduplication and transactions for records/edges/state together. Codex can
reconcile competing claims by superseding either with an active or disputed
replacement, marking that replacement active and preserving both histories.
Four records seeded by direct SQL during installation have no attested actor; each
carries a `provenance` memory_event saying so, without changing the record.
No routine MCP operation deletes records. Owner-directed administrative deletion
must also consider relationships and copies in backups; it is not automated here.

`record_memory_use` appends an attributed report of actual lesson application:
helpful, unhelpful, misleading or uncertain, with an explanation and source.
These are agent reports, not independently verified performance measurements or
votes. They never change the lesson text, status or search ranking. Merely finding
or reading a lesson is not an application outcome. Contributors choose when a
report is useful; no per-task report is required. `get_memory` includes the most
recent 50 audit events, including use reports. Older events remain in the database.

## Disposable test database

For confined coder/reviewer runs, opt into the harness's disposable capability
with `CODEX_LOCAL_MEMORY_TEST_DB=1` and the optional
`CODEX_LOCAL_CONTAINER_IMAGE=codex-local-worker-memory:0.1.0`. Build that image
from the harness's `docker/memory-test-worker` directory first. The harness
supplies `MEMORY_TEST_PROFILE` and `MEMORY_INTEGRATION=1` automatically.

From this directory in the worker, run:

```sh
python3 -m unittest test_memory_service test_memory_test_db
```

The real service tests use a separate in-memory database instance with synthetic
credentials, no published host port, no production data and no Docker socket in
the worker. Explicitly invalid/empty/missing profiles fail closed. Test fixture
cleanup handles setup/teardown failures; the host owns container/network cleanup.
Without the profile variable, the existing operator-only host fixture is retained.
See the harness's `docs/memory.md` for build/launch commands, limits and recovery.
