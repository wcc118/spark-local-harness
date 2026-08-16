# v0.3.0 workflow replay

This fixture exercises the version-two read projection without rewriting the
version-one append-only wire records. It contains:

- issue #35 evidence arriving before any delivery milestone;
- an ordinary coder, reviewer, Codex decision, merge, and post-merge path; and
- a failed review followed by a numbered correction loop to implementation.

The replay is diagnostic evidence. It does not authorize workflow transitions.
