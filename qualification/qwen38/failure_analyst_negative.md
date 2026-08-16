Diagnose only failures supported by the observed evidence. Return exactly one
JSON object matching failure-analysis schema version 1. You are advisory only.

Observed evidence:

- A concurrent file counter reaches the correct final value of 600.
- Readers observe only valid integer contents.
- The cleanup assertion passes.
- A post-run directory listing is empty.
- No lock or temporary artifact remains.

Do not assert a cleanup or lock-file failure contrary to this evidence. Return
`hypotheses`, `known_facts`, and `unresolved_questions`.
