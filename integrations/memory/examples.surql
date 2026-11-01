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

-- ===== Surrealist workflow =====
-- One-time: in the Graph view of any result, "Configure labels" and set
--   memory -> title     topic -> name     project -> name
-- (Surrealist keeps this in your browser, so repeat it per browser profile.)
-- Keep "Stray records" off. The whole-database graph above is a hairball: prefer the scoped
-- graphs below, which return the nodes AND their edge records so relationships draw.

-- Graph: one project (change 'weather'; 'owner/repo' and 'repo' spellings are the same project).
LET $m = SELECT VALUE id FROM memory WHERE array::last(string::split(project ?? '', '/')) = 'weather' AND status IN ['active', 'disputed'];
LET $e = SELECT VALUE id FROM concerns, discovered_in, challenges, supersedes WHERE in IN $m;
SELECT * FROM array::concat($m, $e, (SELECT VALUE out FROM $e));

-- Graph: contested knowledge (disputed claims, their corrections, supersession chains).
LET $m = SELECT VALUE id FROM memory WHERE status = 'disputed' OR id IN (SELECT VALUE in FROM challenges) OR id IN (SELECT VALUE in FROM supersedes) OR id IN (SELECT VALUE out FROM supersedes);
LET $e = SELECT VALUE id FROM challenges, supersedes WHERE in IN $m OR out IN $m;
SELECT * FROM array::concat($m, $e);

-- Graph: everything touching one topic (change the name).
LET $t = (SELECT VALUE id FROM topic WHERE name = 'openfoam');
LET $e = SELECT VALUE id FROM concerns WHERE out IN $t;
SELECT * FROM array::concat($t, $e, (SELECT VALUE in FROM $e));

-- Tables (use the Table result view; titles replace the hashed IDs).
SELECT id, title, kind, status, project, actor, created_at FROM memory ORDER BY created_at DESC LIMIT 50;
SELECT name, count(<-concerns) AS memories FROM topic ORDER BY memories DESC LIMIT 40;
SELECT title, id, project FROM memory WHERE status = 'active' AND array::len(topics) = 0;
SELECT title, id, status, actor, created_at FROM memory WHERE status = 'disputed' ORDER BY created_at;
SELECT target.title AS lesson, outcome, explanation, actor, created_at FROM memory_event WHERE action = 'use' ORDER BY created_at DESC;

-- Search usage: which lessons searches actually return (no query text is stored).
SELECT created_at, actor, repository, mode, count FROM retrieval ORDER BY created_at DESC LIMIT 30;
SELECT created_at, count, results.title AS returned_titles FROM retrieval ORDER BY created_at DESC LIMIT 20;

-- Meaning-based neighbours of one memory (paste its ID); useful for spotting duplicates.
LET $v = (SELECT VALUE vector FROM memory_embedding WHERE memory = memory:3874b2da24650ac6a0219de9cf109885)[0];
SELECT memory.title AS title, memory.status AS status, vector::similarity::cosine(vector, $v) AS similarity FROM memory_embedding ORDER BY similarity DESC LIMIT 10;
