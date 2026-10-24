-- Run in personal / knowledge. These queries only read data.
SELECT * FROM memory WHERE status = 'active' LIMIT 20;
SELECT * FROM memory WHERE text @0@ 'local knowledge' LIMIT 5;
SELECT * FROM memory, project, topic, discovered_in, concerns, supersedes, challenges;
-- Discover existing topics with optional substring matching
SELECT id, name FROM topic ORDER BY name ASC LIMIT 30;
SELECT id, name FROM topic WHERE string::contains(string::lowercase(name), string::lowercase('performance')) ORDER BY name ASC LIMIT 30;
-- Select graph visualization for the last result, or explore a record's relations.
-- Recent use reports for a specific memory record:
SELECT * FROM memory_event WHERE target = type::record('memory:abc123') AND action = 'use' ORDER BY created_at DESC LIMIT 20;
-- Recent enrich events for a specific memory record:
SELECT * FROM memory_event WHERE target = type::record('memory:abc123') AND action = 'enrich' ORDER BY created_at DESC LIMIT 20;
-- All events for a specific memory record:
SELECT * FROM memory_event WHERE target = type::record('memory:abc123') ORDER BY created_at DESC LIMIT 50;
