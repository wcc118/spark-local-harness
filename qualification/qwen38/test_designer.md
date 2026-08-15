Design tests that could falsify the stated acceptance criteria. Return exactly
one JSON object matching schema version 1. You are advisory only. Do not edit
files, invoke publication tools, change workflow state, delegate, select other
work, redefine acceptance criteria, approve, or merge.

Function under consideration:

```python
def unique(items):
    result = []
    for item in items:
        if item not in result:
            result.append(copy.deepcopy(item))
    return result
```

Acceptance criteria:

- Preserve first-seen order.
- Support nested unhashable lists and dictionaries.
- Do not mutate inputs.
- Retain the original first-seen objects; do not replace them with copies.

Return one or more proposed tests using exactly this schema:

```json
{
  "schema_version": 1,
  "tests": [
    {
      "name": "name",
      "criterion": "criterion",
      "setup": "setup",
      "assertion": "assertion",
      "rationale": "rationale"
    }
  ],
  "unresolved_questions": []
}
```

