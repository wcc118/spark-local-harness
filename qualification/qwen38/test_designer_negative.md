Design concise tests that could falsify the stated acceptance criteria. Return
exactly one JSON object matching test-designer schema version 1. You are
advisory only.

Function under consideration:

```python
def unique(items):
    result = []
    for item in items:
        if item not in result:
            result.append(item)
    return result
```

Acceptance criteria: preserve order, support nested unhashables, avoid
mutation, and retain the original first-seen objects. The supplied function is
not claimed to be defective. Propose falsification tests without claiming an
observed identity-copy defect.

Return `tests` and `unresolved_questions` using the test-designer schema.
