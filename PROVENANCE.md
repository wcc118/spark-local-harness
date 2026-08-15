# Provenance

This repository was extracted on 2026-08-15 from the operational harness at:

`/home/connellwc83/projects/codex/local-harness`

The source directory was not a Git repository, so no file history was available
to split. The initial import preserves the operational Python source, tests,
entrypoints, frozen profiles, and legacy rollback artifacts while excluding
benchmark task packets, generated caches, and run scratch directories.

The imported suite passed `114` tests before extraction. The same suite must
pass in this repository before the `baseline-v2-extracted` tag is created.

The frozen benchmark identity remains:

- Repository: `wcc118/codex-harness-benchmark`
- Seed commit: `6aa7e82530f55c1628593952b18ef46baef072b0`
- Operational profile: `profiles/baseline-v2.freeze.json`

No credentials, model weights, runtime homes, or retained inference logs are
part of the import.

