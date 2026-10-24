-- Run in personal / knowledge. These queries only read data.
SELECT * FROM memory WHERE status = 'active' LIMIT 20;
SELECT * FROM memory WHERE text @0@ 'local knowledge' LIMIT 5;
SELECT * FROM memory, project, topic, discovered_in, concerns, supersedes, challenges;
-- Select graph visualization for the last result, or explore a record's relations.
-- Recent use reports for a specific memory record:
SELECT * FROM memory_event WHERE target = type::record('memory:abc123') AND action = 'use' ORDER BY created_at DESC LIMIT 20;
