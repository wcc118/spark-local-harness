# Worker completion ownership

An issue worker remains owned by its calling Codex session until the caller has
observed the harness exit, inspected its result, and recorded its next action.
Model idleness, a loaded endpoint, a stopped inference request, and runtime exit
do not establish successful delivery. Finalization can still fail.

## Caller contract

Set `CODEX_LOCAL_RECORD_RUN=1`, `CODEX_LOCAL_REPOSITORY`, and
`CODEX_LOCAL_ISSUE` on every issue-bound call; reviewers also set `CODEX_LOCAL_PR`.
Preserve `CODEX_THREAD_ID`, or supply the actual calling session through
`CODEX_LOCAL_SESSION_ID`. Never use an unrelated session identity.

Launch through the stable worker entry point and retain the tool's execution
session ID. If `functions.exec` yields a cell, first resume that cell with
`functions.wait`; once `exec_command` returns a running session, keep polling
that session with `write_stdin` until exit. Keep waits at 30 seconds so progress
updates and user steering remain possible. Do not end the turn while waiting.
Never use `&`, `nohup`, or another detached launch without a separately verified
controller that owns completion delivery. Long timeouts do not provide monitoring.

If the original execution handle is lost, inspect recorded progress:

```bash
codex-local-completion status --wait 30
```

This bounded command polls the ledger; it does not attach to or restart a model.
It returns after at most 30 seconds even if still pending. Read `state` and repeat
as needed. Once terminal, inspect the retained result JSON/event log and current
GitHub issue/PR/head. Record that inspection and the next action:

```bash
codex-local-completion ack RUN_ID --disposition 'Exit 65; malformed review inspected; escalated for correction, no publication'
```

Use `--session SESSION_ID` before the subcommand only for deliberate recovery of
that session. Acknowledgement is a receipt of human/agent inspection, not a
machine verification that the disposition is true, nor approval or merge
authorization. The command never retries, publishes, changes labels, or rewrites
results. Continue authorized work after acknowledgement or report a concrete
blocker. Invalid reviews stay invalid.

## Stop guard

The `Stop` hook uses exact session IDs in existing workflow records, corroborated
by lifecycle exit evidence. A recorded active run or an unacknowledged terminal
run produces a continuation asking Codex to wait or inspect the result. The guard
continues to apply after an earlier Stop continuation. Other sessions' valid
records have no effect. Lifecycle completion alone cannot acknowledge pending
workflow finalization or delivery. Changed evidence invalidates an old receipt.

This guard covers recorded runs only, beginning at the wrapper's workflow-start
record. It cannot discover unrecorded launches or failures before that record.
Missing/corrupt terminal evidence is never interpreted as success: investigate
the wrapper process, logs, and telemetry errors. A stopped model is insufficient.
Corrupt ledger input blocks the check conservatively and reports the evidence
error. Explicit user interruption is preserved; no Interrupt hook is installed.

Install the CLI from `bin/codex-local-completion` and the module in the qualified
runtime, then add this handler to the user's existing `hooks.json` without
replacing other hooks:

```json
{"hooks":{"Stop":[{"hooks":[{"type":"command","command":"/home/connellwc83/.local/bin/codex-local-completion stop-hook","timeout":10,"statusMessage":"Checking local worker completion"}]}]}}
```

Codex requires review/trust of new user hooks in `/hooks`. Installation alone
does not activate an untrusted hook. Verify the hook is loaded and trusted in the
calling client; an existing remote session may need to reload/resume. The guard
does not wake a turn that already ended. A remote client that does not load/run
this hook must follow the explicit wait contract; do not claim guard enforcement
there without an observed test. Source: https://learn.chatgpt.com/docs/hooks.

For isolated qualification only, Codex documents
`--dangerously-bypass-hook-trust` for an invocation whose hook source was already
reviewed externally. Do not add that flag to normal production launches.

Rollback: remove only this Stop handler, restore the backed-up CLI/runtime and
caller instructions, and retain acknowledgement records as incident evidence.
Do not delete other hooks or change model routing.

## Incident 177

Weather review run `20260906T185038Z-882842` was launched by session
`01a077ad-debc-78f3-885d-85bd5906bebe`. The caller polled execution session `23969`
three times, then completed its turn at 18:53:41 UTC on 2026-09-06. The harness
recorded terminal exit 65 at 19:01:17 UTC: malformed review, no publication.
The installed bridge matched the pre-170 source. The result used a commentary
string for test provenance and omitted a required exit code for another test.
The prompt now states these validator requirements explicitly; validation is
unchanged. Existing failed reviews are not repaired or published automatically.
