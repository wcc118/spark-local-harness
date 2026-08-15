Map only the repository evidence relevant to this bounded task. Return exactly
one JSON object matching schema version 1. You are advisory only. Do not edit
files, invoke publication tools, change workflow state, delegate, select other
work, redefine acceptance criteria, approve, or merge.

Task: In this repository, identify the implementation and test files governing
normalized health telemetry, Codex merge decisions, and merge publication.
Recommend the smallest scope for changing health thresholds without changing
merge authority.

Return exactly these fields:

```json
{
  "schema_version": 1,
  "relevant_files": ["repository/relative/path"],
  "symbols": ["symbol"],
  "existing_tests": ["repository/relative/path"],
  "constraints": ["constraint"],
  "uncertainties": ["uncertainty"],
  "recommended_scope": ["scope statement"]
}
```

