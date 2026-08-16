# v0.3.0 workflow replay

This fixture exercises the version-two read projection without rewriting the
historical version-one append-only wire records. Live publications use the new
version-two actor/source/milestone contract. The fixture contains:

- issue #35 evidence arriving before any delivery milestone;
- an ordinary coder, reviewer, Codex decision, merge, and post-merge path; and
- a failed review followed by a numbered correction loop to implementation.

The replay is diagnostic evidence. It does not authorize workflow transitions.
