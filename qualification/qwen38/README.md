# Qwen3.8-27B advisory qualification

These fixtures qualify roles independently. A passing content check is not a
role promotion by itself.

The initial run found:

- Repository mapping through the current OpenCode helper was useful but far too
  expensive and schema-incompatible.
- Test design, failure analysis, and scouting each recalled the seeded defect in
  one direct LM Studio trial.
- All three direct trials wrapped the requested object in exactly one JSON code
  fence, so raw schema adherence was zero. The candidate may normalize that one
  bounded form while still rejecting prose and multiple blocks.
- No role is promoted until repeated fixtures establish recall, false-positive
  behavior, timing, and normalized schema reliability.

Raw model responses and event logs remain outside Git because they contain
unbounded generated content. `initial-results.json` retains normalized metrics
and conclusions.

