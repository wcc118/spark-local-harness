# Candidate local convergence (#181)

This is an opt-in candidate, not a production installation. Start with
`bin/codex-local-worker-candidate ROLE REPOSITORY --task-file PACKET`. It uses the
same model routes as production. Set `CODEX_LOCAL_RECORD_RUN=1`, repository and
issue identity, and preserve the calling `CODEX_THREAD_ID`. The caller still owns
the entire execution through terminal inspection and completion acknowledgement.

Coder and reviewer whole-task deadlines default to zero (disabled); an explicit
`CODEX_LOCAL_TIMEOUT_SECONDS` enables an operator-requested deadline. Helper's
existing default remains unchanged. Individual command/network timeouts still
report failures. The model chooses its investigative sequence. Duration is not
evidence of a reasoning loop. No automatic frontier takeover or two-cycle stop
is implemented.

## Observation and intervention

The attached launcher mechanically emits compact event counts while the worker
runs. Quiet periods generate no activity messages. This is not frontier inference
and does not claim test acceptance. `bin/codex-local-supervise status RUN --offset N`
returns bounded deltas; `watch RUN` streams the same observations. Full private
logs remain available for deliberate inspection. Codex should assess material
changes, requests and periodically sampled status; do not resend the entire log
or infer that an empty delta is completion. Client tool-wait behavior still controls
frontier wakeups; no claim of automatic inference-cost savings is made.

`guide RUN --file ADVICE` records bounded, private guidance for checkpoint restart.
The current adapter does not claim live injection into native Qwen Code. To redirect
active work, use `cancel RUN`, consume the terminal result, and acknowledge it.
Then launch the candidate with `CODEX_LOCAL_RESUME_RUN=RUN` and a current task packet.
This resumes the retained coder checkout, not the previous model conversation.
Guidance moves from queued to submitted; only an emitted receipt is recorded as
model-reported acknowledgement. A receipt does not prove the guidance was followed.
Foreign-session control and concurrent execution in the same retained checkout
are rejected. Reviewer runs always start from a fresh exact revision.

`checkpoint RUN` describes retained work after termination. Runtime credentials are
removed; implementation checkouts and candidate reviewer diagnostics survive. Never
delete retained work until its publication or explicit abandonment is established.
Abrupt host loss/SIGKILL may leave a pending ownership record: investigate the
recorded PID/start identity, retained checkout and logs; never synthesize success.

## Investigative review and verification

Candidate reviewers explicitly select `convergence-reviewer`, with search, read
and shell tools in the container. The submitted checkout remains read-only;
diagnostics/test copies belong in the mounted scratch directory. Postflight still
checks exact head, checkout integrity, tool scope, result identity and publication.
The reviewer may inspect omitted diff context instead of being limited to a packet.
No new reviewer step cap or aggregate token threshold is imposed by this candidate;
an explicitly supplied token threshold remains available. Provider output/context
limits still apply and are not task deadlines.

OpenCode's `doom_loop` permission is allowed for this reviewer so its repeated-tool
heuristic cannot unilaterally terminate the task. Repetition diagnostics still go
to the event log; Codex assesses whether new evidence is emerging and supplies
guidance or requests escalation. Permission to continue is not a claim that a loop
is productive. The investigative packet omits host-only executable discovery;
successful explicit capability checks are reported from the worker container.

Choose a pre-provisioned image with `CODEX_LOCAL_CONTAINER_IMAGE`. Supply an optional
`CODEX_LOCAL_VERIFY_COMMAND` to validate required capabilities inside that same
confined image before inference. The harness does not install packages at runtime.
Codex owns choosing the actual verification contract and resolving requirements.

## Outcome and accounting

`ownership.jsonl` is written before inference independently of workflow projection.
`execution.jsonl` is written at final cleanup. Completion acknowledgements use that
terminal record for candidate runs even if workflow projection failed. Each attempt
gets a distinct default operation ID, avoiding issue-wide milestone regression;
repository/issue/session/run and resume references provide durable identity.

Candidate `usage.jsonl` is canonical, replay-idempotent and explicitly complete,
partial or unknown. It preserves runtime totals and cache-inclusive/exclusive views
without adding reasoning twice. Unknown is null. Legacy CSV writes are skipped for
candidate runs; old data is not rewritten. Health rows expose canonical usage and
unknown intervention rather than assuming autonomy. Execution success, published
artifact, review verdict and acceptance remain distinct; acceptance needs independent
evidence. Existing adjudication records remain the place for primary decisions.

Bracket a deliberate frontier assessment with
`PYTHONPATH=src python3 -m codex_local_harness.assessment_usage start --run RUN --assessment ID --kind monitoring`
and the same command with `finish`. Kinds also include `guidance`, `review` and
`recovery`. The command reads only scalar cumulative token counters from the calling
Codex rollout and writes `assessments.jsonl`; it does not invoke a model. Missing
starts/counters and counter resets remain unknown. A delta covers **all work in
that Codex session during the interval**. Do not sum overlapping intervals or call
an interval pure monitoring while doing implementation in the same session. These
are tokens, not dollars; cached inputs and reasoning are separately reported and
reasoning is not added again. Candidate dashboard frontier totals remain unknown
until per-run attribution exists; the assessment ledger provides interval evidence.

## Durable project state and qualification

At meaningful transitions update the GitHub issue/PR with exact revision, observed
tests, unresolved findings, decisions and next action. Keep process handles and raw
logs in private harness records. Do not place credentials or raw conversation in
GitHub. A fresh Codex session consults GitHub first; cross-session recovery requires
explicit ownership reconciliation, not weakening the current session checks.

Qualification uses frozen baseline artifacts listed in
`qualification/convergence/baseline.json`, synthetic issue #182 and identical task
packets/starting trees. Local models are test subjects only. Record observed results
and infrastructure failures without silent retries. No flight-loads work, model
upgrade, DSH runtime migration, custom Codex compaction, merge, or production
promotion is part of this candidate.
