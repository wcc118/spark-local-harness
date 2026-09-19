# Role suite v2

This frozen, deterministic suite evaluates bounded local-worker roles without
placing oracle answers in model-visible workspaces. It contains 62 cases:

- reviewer: 20 cases, including interacting defects and clean controls;
- investigator: 8 cases;
- test strategy: 6 cases;
- non-coding analysis: 24 cases across six categories;
- workflow classification: 4 secondary controls.

The runner creates an isolated workspace containing only `evidence.json`, asks
OpenCode to write `result.json`, and scores the result with an external exact-set
oracle. Correctness, terminal completion, timeout, elapsed time, tool calls,
tool errors, and reported tokens are recorded separately. Attempt identity binds
the public case, private oracle, and prompt policy. Model, policy, and repeat are
included in the output path, preventing collisions.

Run the unit and fake-runtime integration tests with:

```bash
python3 -m pytest -q tests/test_role_suite_v2.py
```

Verify the frozen corpus with:

```bash
python3 -m qualification.role_suite.v2.run verify
```

Run data is intentionally stored outside the repository. Issue #165's retained
evidence is under `~/.local/share/codex-local-harness/benchmark-165/role-suite-v2`.

## Known limitation

`review-clean-01` and `review-clean-04` proved ambiguous during the frozen run:
returning `None` can still be considered a weak null-handling contract, and
returning `False` after a failed commit can still be considered swallowed-error
behavior. Raw results retain these cases, but role decisions must also report a
sensitivity analysis excluding them. The four workflow cases are exact-set
classification controls, not substitutes for real repository implementation
trials, and are secondary evidence only.
