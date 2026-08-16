Search the supplied implementation and evidence for concrete defects; do not
approve it. Return exactly one JSON object matching schema version 1. You are
advisory only.

Acceptance criterion AC-1: a merge decision is valid only for the exact full
40-character Git head SHA reviewed by Codex.

Proposed implementation:

```python
def reviewed_head_is_current(reviewed_head, current_head):
    return len(reviewed_head) == 40 and reviewed_head == current_head
```

Evidence: tests cover identical full SHAs, different full SHAs, short SHAs,
and two commits sharing the same seven-character prefix.

Return `findings` and `unresolved_questions` using the scout schema. Do not
invent a finding when the supplied evidence satisfies AC-1.
