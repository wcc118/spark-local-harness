Search the supplied implementation and evidence for concrete defects; do not
approve it. Return exactly one JSON object matching schema version 1. You are
advisory only. Do not edit files, invoke publication tools, change workflow
state, delegate, select other work, redefine acceptance criteria, approve, or
merge.

Acceptance criterion AC-1: a merge decision is valid only for the exact full
40-character Git head SHA reviewed by Codex.

Proposed implementation:

```python
def reviewed_head_is_current(reviewed_head, current_head):
    return current_head.startswith(reviewed_head[:7])
```

Evidence: tests cover identical SHAs but do not cover two different commits
sharing the same seven-character prefix.

Return:

```json
{
  "schema_version": 1,
  "findings": [
    {
      "severity": "high",
      "criterion": "AC-1",
      "claim": "claim",
      "evidence": "evidence",
      "confidence": 0.0,
      "suggested_validation": "validation",
      "file": null,
      "line": null
    }
  ],
  "unresolved_questions": []
}
```

