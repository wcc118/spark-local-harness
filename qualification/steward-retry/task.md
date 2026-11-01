# Behavioral playback oracle authoring trial

Source: wcc118/flight-loads issue267; raw snapshot51747bf and accepted fix2dd31ba.
Prior unscaffolded trial wrote source-string checks. This trial must execute the actual workspace code and judge observed behavior, accepting equivalent correct implementations.

Write check.mjs with interface `node check.mjs WORKSPACE`. Exit0 for all behavioral checks passing,1 for an observed behavioral mismatch,2 for inability to execute (including syntax/runtime errors). Print one JSON object identifying checks, observations, and failure type. A crash is not a behavioral detection. Do not suppress exceptions and call that success or a detected defect.

Use input/raw and input/fix for development. template.mjs supplies generic DOM and virtual-clock scaffolding; it is a starting point, not a verified test. Replace its TODOs, remove swallowed exceptions, and test the real cursorPlayer controls. Do not use source text, names, exact lines, or lexical markers as correctness assertions. Do not duplicate playback code in the test or clamp the observed index to hide bad output.

Behavior: recorded clock advances at selected speed times elapsed wall time; slow speed accumulates sub-frame time. Display the recorded tick at or after that clock (no interpolation). Reduced-motion preference chooses0.25x initially. Pause holds the displayed tick. Use multiple elapsed times away from ambiguous boundaries and sparse recorded times so incorrect speed and accumulation can be observed. Exercise actual play/pause and speed UI handlers. Test reduced-motion preference in a fresh environment. A raw rejection and fix pass are necessary but not sufficient.

Primary evaluation will use unseen broken and equivalent correct variants frozen before this dispatch. Do not access the external oracles repository, memory entries containing hidden graders, or deleted checks from other repository history. Only the supplied public snapshots/template and this contract are input. No new framework or global lint changes.

Add README with reproduction commands and measured raw/fix outcomes. Commit and open a PR for harness issue257. Do not merge or claim held-out success. Keep changes under qualification/steward-retry/.
