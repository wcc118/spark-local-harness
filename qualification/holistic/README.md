# Structured-issue holistic benchmark v1

This layer connects the existing ten-case executable coding corpus and the
62-case specialist corpus to the issue-driven harness. The task-specific facts
are rendered as a structured GitHub-style issue; a reusable role contract is
prepended by the specialist operation. Hidden evaluators remain outside worker
workspaces.

The frozen run plan is `plan.json`. Development results select a policy before a
fresh confirmation run. The controlled comparison uses the same policy for both
models; the harness comparison uses each model's selected policy. Raw artifacts
belong under the plan's external output root, not in git.

## Executable commands

Coding policy or confirmation run:

```bash
python3 qualification/paired-model/run.py \
  --output OUTPUT run --model MODEL --route ROUTE \
  --variant POLICY --instruction-prefix POLICY_FILE --repeat 1 --timeout 600
```

Specialist development or confirmation run:

```bash
python3 -m qualification.role_suite.v3.runner run \
  --output OUTPUT --opencode opencode --model MODEL --route ROUTE \
  --corpus challenge --policy POLICY --repeat 3 --resume
```

Use `--corpus full --repeat 1` for the locked confirmation. A hybrid confirmation
is invoked once per suite with its selected policy into the same output root.

Managed lifecycle run:

```bash
python3 -m qualification.holistic.run \
  --output OUTPUT --opencode opencode --model MODEL --route ROUTE --repeat 1
```

Final scorecard after copying or linking the locked confirmation artifacts into
`coding-confirmation/{coder-next,deepseek}`,
`specialist-confirmation/{coder-next,deepseek}`, and
`lifecycle/{coder-next,deepseek}` beneath the run root:

```bash
python3 -m qualification.holistic.scorecard --root RUN_ROOT
```

Every runner supports `--resume`; resume refuses a changed prompt/corpus identity.
A correct artifact produced only after a timeout is recorded as artifact-correct
but not completed-and-correct. Correctness determines the score; elapsed time and
tokens are reported as operational costs only. Estimated Codex-token displacement
uses the explicit assumptions in `plan.json` and is never described as observed
billing.
