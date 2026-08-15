Diagnose the observed failure and propose evidence-gathering checks. Return
exactly one JSON object matching schema version 1. You are advisory only. Do not
edit files, invoke publication tools, change workflow state, delegate, select
other work, redefine acceptance criteria, approve, or merge.

Observed evidence:

- A concurrent file counter reaches the correct final value of 600.
- Readers observe only valid integer contents.
- The hidden evaluator still fails its cleanup assertion.
- After the run, the directory contains `.count.lock`.
- The acceptance criteria require no temporary or lock artifacts after all
  writers complete.

Return:

```json
{
  "schema_version": 1,
  "hypotheses": [
    {
      "claim": "claim",
      "evidence": "observed evidence",
      "confidence": 0.0,
      "next_check": "bounded check"
    }
  ],
  "known_facts": ["fact"],
  "unresolved_questions": ["question"]
}
```

