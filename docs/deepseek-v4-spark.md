# DeepSeek V4 Flash candidate on one DGX Spark

Issue #163 qualifies the EXL3 K2 candidate without changing production routing.
The managed wrapper pins image digest
`sha256:86f99e42f2061322e7bc47691e09d9ebbef22951ec8aebd7662f3d1da1a535bc`
and expects the upstream recipe at commit
`9b2b1e3df1429d08e7693ae720de06453ab0c375` in
`/home/connellwc83/deepseek-v4-flash-0731-exl3-k2-spark`.

Copy `config/deepseek-v4-compose.codex.yaml` into that recipe as
`compose.codex.yaml`. The overlay binds the API only to `127.0.0.1:8011`,
disables automatic restart, and retains the upstream qualified Spark settings.

The wrapper refuses startup while production responds on port 8000, while the
candidate already responds on port 8011, or while less than 90 GiB is available.
It never stops production itself. Enter and leave a model maintenance window
explicitly, and restore Coder-Next with `codex-local-model use coder-next`.

```bash
codex-local-deepseek-v4 pull
codex-local-deepseek-v4 preflight
codex-local-deepseek-v4 start
codex-local-deepseek-v4 status
codex-local-deepseek-v4 stop
```

No production worker route selects this candidate. Promotion requires a frozen
direct-coder comparison and a separate merge decision.
