# Qwen3.8-27B advisory qualification

These fixtures qualify roles independently. A passing content check is not a
role promotion by itself.

The completed qualification found:

- Repository mapping through the current OpenCode helper was useful but far too
  expensive and schema-incompatible.
- Test design, failure analysis, and scouting each recalled the seeded defect in
  all three positive direct LM Studio trials.
- All three direct trials wrapped the requested object in exactly one JSON code
  fence, so raw schema adherence was zero. The candidate may normalize that one
  bounded form while still rejecting prose and multiple blocks.
- Scout passed its negative control and all four outputs validated after the
  deliberately narrow single-fence normalization. It is promoted only for
  supplied-evidence, direct-LM-Studio scouting; it has no tools or repository,
  GitHub, workflow, approval, or merge authority.
- Test designer and failure analyst are not promoted. Each failed schema
  validation on its negative control. The failure-analysis schema also cannot
  currently express the valid result "no supported failure hypothesis."
- Mapper is not promoted after three OpenCode trials. It found relevant content,
  but tool-loop cost and schema incompatibility make it unsuitable for this role.

Raw model responses and reasoning remain outside Git because they contain
unbounded generated content. `initial-results.json` preserves the first window;
`final-results.json` contains the normalized repeated-trial evidence and final
role decisions.
