# Focused shared-memory reading

Use the existing Surrealist connection to the shared `knowledge` database.
Designer displays schema; it is not the lesson reader. Open a query tab, paste a
query from `qualification/memory-views/presets.json`, and save it as a named query
in your client. These are ordinary bounded SELECT queries, not an automatic
Surrealist settings import. No listener, port, browser credential or write API
is added. Keep existing credentials in the established connection, not these files.

Start with recent lessons to see what changed. Read the title, full claim,
conditions and source together. Disputed findings need their challenges inspected
before use. Untagged records identify curation candidates, not bad knowledge.
Cross-project topics show where lessons may transfer; sharing a topic is not
support for a claim. Correction chains show directed challenges and supersessions.
Recent use reports distinguish a lesson being applied from merely being returned
by search; helpfulness is an attributed observation, not independent proof.

For one lesson, choose its exact memory ID and set a query variable, for example:

```surql
LET $memory_id = 'memory:REPLACE_WITH_ACTUAL_ID';
```

Then paste `lesson-detail`. It returns the claim, direct correction/supersession
edges, and attributed use reports. To inspect one additional hop, choose a linked
ID and repeat. This keeps the view bounded and readable. Inspect both source and
conditions before transferring the lesson to another project. Graph centrality,
node size and self-reported usefulness are not confidence scores.

These presets inspect shared knowledge only. Private oracle records require the
separate authorized connection and must not be copied into shared views. Editing
or reconciliation uses existing audited MCP contribution tools; no SQL mutation
presets are supplied. Permanent deletion still requires owner direction.

The first increment is useful queries in the existing viewer. A companion UI is
not justified until actual browsing friction is observed; the presets do not
claim a new graph UI or that usability has been owner-tested.
