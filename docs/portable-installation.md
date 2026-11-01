# Install the convergence harness

The `harness-convergence-2026-09-19` release tag is the reproducible installation
source. It contains the qualified convergence implementation, comparison evidence,
and portable packaging. `main` is not yet the installed release: the underlying
PR stack remains open. Do not clone an arbitrary branch and assume it reproduces
production.

## Bootstrap another Linux workstation

Install Python 3.11+, Bash, git, GitHub CLI, jq, curl, util-linux (flock and
setsid), GNU coreutils, Docker, and OpenCode 1.18.21. The qualified Codex version
was 0.155.1. Authenticate GitHub CLI with the intended account and configure Git
commit identity. Add `$HOME/.local/bin` to PATH. Credentials stay outside this
repository and must be provisioned separately.

```bash
git clone https://github.com/wcc118/codex-local-harness.git
cd codex-local-harness
git checkout harness-convergence-2026-09-19
# Build the execution container separately from the model servers.
docker build -t codex-local-worker:0.3.0 docker/worker
bin/install-harness install
bin/install-harness verify
```

The Dockerfile pins Qwen Code 0.22.0. Its base tag and OS packages are not fully
locked, so a rebuild is not a bit-identical copy of the qualified image. The
qualified local image ID is recorded in `config/portable-release.json`; use
`docker save`/`docker load` to transfer that exact image to compatible hardware
when bit-identical container reproduction is required. The installer verifies
all packaged runtime files against the committed manifest. It does not install
Docker, download model weights, provision model servers, or authenticate clients.

The 0.3.0 image adds a native build toolchain (`build-essential`, `gfortran`),
the `libgfortran5`/`libX11` runtime libraries and NumPy 2.x/Matplotlib, so
workers can compile and run repository engines and scientific test suites.
The container mounts `/tmp` without exec permission; compile into the worker home.

### Read-only tool mounts

Host-built tools can be exposed to workers read-only through
`~/.config/codex-local/sandbox-mounts.json` (override with
`CODEX_LOCAL_SANDBOX_MOUNTS`):

```json
{"schema": 1,
 "readonly_mounts": [
   {"source": "/home/USER/.local/share/flight-loads/engines",
    "target": "~/.local/share/flight-loads/engines"}]}
```

Sources must be existing, symlink-free directories that are not the home
directory, an ancestor of it, credentials or configuration (`.ssh`, `.config`,
`.codex`, `.claude`, `.aws`, `.docker`, ...), harness state, or system paths.
Targets must lie inside `~/.local/share/` of the worker home. Every mount is
read-only. Any invalid entry stops the worker before a container starts. A
missing file means no extra mounts. Remove the file to roll back.

The default install assumes separately provisioned compatible model endpoints
and disables automatic model switching. Configure
`~/.config/codex-local/workstation.env`, using exported environment variables:

```bash
export CODEX_LOCAL_VLLM_ENDPOINT=http://127.0.0.1:8000
export CODEX_LOCAL_DEEPSEEK_ENDPOINT=http://127.0.0.1:8011
```

Endpoints exclude `/v1`; workers append it. The coder route expects
`Qwen/Qwen3-Coder-Next-FP8`, and the reviewer expects
`deepseek-v4-flash-0731-exl3-k2`. Route IDs are in
`~/.config/codex-local/workers.json`. These are Linux host-network container
endpoints; Docker Desktop networking requires separate validation.

On a Spark with the existing model recipes provisioned, install with
`bin/install-harness install --model-switch 1`. This option initializes a new
workstation file; an existing file is preserved. The default recipe paths are
`$HOME/spark-vllm-docker/run-recipe.sh` and
`$HOME/deepseek-v4-flash-0731-exl3-k2-spark`. Override
`CODEX_LOCAL_RECIPE_LAUNCHER` and `CODEX_LOCAL_DEEPSEEK_RECIPE_DIR` as needed.

Model servers run as isolated Linux host-network containers. The vLLM runtime
supports GPU memory control via the optional `CODEX_LOCAL_VLLM_GPU_MEM` environment
variable. When unset, the manager defaults to `0.82` for Coder-Next and qwen38
profiles. The `qwen35-nvfp4` profile always uses `0.82` and ignores this setting.
Valid values are finite numbers in the interval `(0.5, 0.95]`. An empty value,
nonfinite value (NaN, Inf), or value outside the range causes the launch to fail
before any container mutation. This setting does not affect the DeepSeek backend,
which uses its own pinned recipe with `0.85`.

### DeepSeek deployment

The DeepSeek backend uses a separate pinned recipe and compose configuration.
The DeepSeek image digest and compose overlay are in the shipped script/config;
weights and the underlying recipe repository are separate hardware prerequisites.
See [DeepSeek deployment notes](deepseek-v4-spark.md),
and inspect `bin/codex-local-deepseek-v4` before provisioning that backend.

## Installed behavior and state

Stable `codex-local-worker`, coder, reviewer, helper and investigator commands
load the versioned supervised runtime. Coding and review default to **zero
whole-task timeout (unlimited)**. An explicitly supplied positive
`CODEX_LOCAL_TIMEOUT_SECONDS` still imposes a deadline. Helper's existing
300-second bound is unchanged. Transport, startup, lock and cancellation grace
periods are not coding/review task deadlines.

Supervision retains incremental progress records without model inference.
Codex continues polling the attached command, reads evidence when needed, and
supplies focused guidance. Monitoring itself does not infer, but Codex polling
turns still incur frontier tokens. Unpublished work is retained. A continuation
reuses that work and a compact packet; it is not a resumed model conversation.
There is no new Codex compaction mechanism.

Always preserve `CODEX_THREAD_ID`. For issue-bound calls, supply
`CODEX_LOCAL_RECORD_RUN=1`, `CODEX_LOCAL_REPOSITORY=OWNER/REPO`, and
`CODEX_LOCAL_ISSUE=N` (plus PR identity for review). Follow the ownership and
acknowledgment contract in [worker completion](worker-completion.md). GitHub
issues, commits, PRs and comments retain project state across sessions.

An existing OpenCode configuration is referenced without copying credentials or
overwriting personal settings; a fresh installation gets a dedicated minimal
worker template. Role prompts are bundled. Existing routes and workstation
settings are preserved. The complete release lives under
`~/.local/share/codex-local-harness/runtime/<manifest-hash>/`; stable wrappers
reference it directly. Source checkout removal does not break the installation.
`runtime/current` also points to it for existing diagnostic integrations.

## Hooks and verification

The installer writes `~/.config/codex-local/hooks.example.json`. Merge its Stop
hook into your Codex hooks configuration, preserving other hooks. Review and
trust new or changed definitions with `/hooks` in the calling client. Installation
alone does not enable/trust a hook or wake an ended turn. Existing unchanged
trusted hook definitions need no rewrite. See the official
[Codex hooks documentation](https://developers.openai.com/es-419/docs/hooks).

```bash
bin/install-harness verify
codex-local-supervise --help
codex-local-completion status
```

Verification checks release bytes, stable commands and the runtime pointer; it
does not certify endpoint health or a fresh machine's full model execution.
The portable installer tests use a clean target home with spaces and verify
source-independent dispatch, integrity rejection and exact rollback. Live
model convergence evidence is in `qualification/convergence-184/REPORT.md`.
Requalify your endpoints/container with a small synthetic issue before using a
new hardware deployment for unattended production work.

The optional diagnostic dashboard/service is not automatically installed or
restarted. Existing services referencing `runtime/current` will use the new
source on their next restart. Inspect the service template separately if needed.

## Rollback and release maintenance

Installation takes the model lock and refuses if a model worker holds it. Install
only when other harness workers are idle, including externally managed endpoints
that do not use the model lock. Every overwritten command and prior runtime
pointer is backed up; absent files are recorded too. Installation failure restores
the prior files. Runtime data and release bundles are retained.

```bash
bin/install-harness rollback
```

Rollback refuses if a managed command was subsequently changed. It restores the
previous commands, configuration files created by installation, receipt and
runtime pointer. Save any edits to newly created configuration before rollback.
The install receipt records the exact backup location and release hash.

Maintainers regenerate `config/portable-release.json` with
`bin/build-portable-manifest` only after reviewing runtime changes, then run tests
and commit the manifest alongside those changes. Users should not regenerate a
manifest to bypass an integrity failure. The manifest's `qualified_base` names
the comparison evidence base, not a claim that later packaging edits received
an independent model A/B run.
