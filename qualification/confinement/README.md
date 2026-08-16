# Isolated checkout qualification

The confinement regression creates a disposable Git repository, lets a fake
coder mutate the checkout presented by the worker, and verifies the source
checkout's content, HEAD, index, and worktree remain byte-for-byte unchanged.
The disposable clone is removed on normal exit; the same cleanup trap covers
interruptions and timeouts.

The live delivery regression for issue #79 runs the deterministic coder
postflight from an isolated clone against the already-open PR. It must push the
exact branch and commit, rediscover the PR, and transition the issue to review
without changing the orchestrator checkout.
