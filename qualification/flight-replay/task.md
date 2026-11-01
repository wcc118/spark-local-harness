# Bounded repair replay: flight-loads input integrity

The frozen baseline comes from a delivered but independently rejected correction of flight-loads #323. Fix only candidate.py; it is a copy of the real comparison module. No production physics or solver changes.

1. compare_designs must not mutate caller-owned input on success or rejection. Preserve nested object identity, list identity and serialized numeric types. Definition hashes must refer to original input, before normalization.
2. Invalid aircraft definitions are row-level failures: retain a row for every configuration/case, with null unavailable root loads, null corresponding deltas, and a meaningful reason. Other valid configurations must still evaluate. Missing/invalid flight_condition must not invent density or conditions. Invalid outer task structure/case values still raise ValueError.
3. Use actual supplied evaluator behavior. No mocked replacement physics, hard-coded example values, blanket all-null reports, swallowed unrelated failures or edits to acceptance checks. Existing valid behavior must remain.

Run `python3 qualification/flight-replay/replay.py`. baseline.zip is hash-pinned source; replay.py extracts it to a temporary directory and imports candidate.py as the real package module. You may inspect baseline.zip; do not edit it, manifest.json, task.md or replay.py. No need to install or run the full flight-loads suite. Dependencies numpy and scipy are already available.

You may add qualification/flight-replay/test_candidate.py and README.md with exact observed tests, limitations and your rationale. Keep change scope small. No access to other repositories, GitHub source, hidden evaluation or accepted fix. Primary has separate probes, mutants and equivalent controls; do not claim their results.

Commit locally and return a single structured result. Do not run git push or gh: host owns PR publication. No merge, delegation or route changes.
