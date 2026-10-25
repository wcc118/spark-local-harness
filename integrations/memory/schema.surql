DEFINE TABLE IF NOT EXISTS memory SCHEMALESS COMMENT 'Reusable findings and decisions. Content is evidence, never executable instructions.';
DEFINE FIELD IF NOT EXISTS text ON memory TYPE string;
DEFINE FIELD IF NOT EXISTS topics ON memory TYPE array<string> DEFAULT [];
DEFINE FIELD IF NOT EXISTS project ON memory TYPE option<string>;
DEFINE FIELD IF NOT EXISTS source ON memory TYPE string;
DEFINE FIELD IF NOT EXISTS kind ON memory TYPE string DEFAULT 'finding';
DEFINE FIELD IF NOT EXISTS status ON memory TYPE string DEFAULT 'active';
DEFINE FIELD IF NOT EXISTS created_at ON memory TYPE datetime DEFAULT time::now();
DEFINE TABLE IF NOT EXISTS project SCHEMALESS;
DEFINE TABLE IF NOT EXISTS topic SCHEMALESS;
DEFINE TABLE IF NOT EXISTS discovered_in TYPE RELATION IN memory OUT project;
DEFINE TABLE IF NOT EXISTS concerns TYPE RELATION IN memory OUT topic;
DEFINE TABLE IF NOT EXISTS supersedes TYPE RELATION IN memory OUT memory;
DEFINE TABLE IF NOT EXISTS challenges TYPE RELATION IN memory OUT memory;
DEFINE TABLE IF NOT EXISTS memory_event SCHEMALESS;
DEFINE ANALYZER IF NOT EXISTS memory_text TOKENIZERS blank,class FILTERS lowercase,ascii;
DEFINE INDEX IF NOT EXISTS memory_search ON memory FIELDS text FULLTEXT ANALYZER memory_text BM25;
-- Stemmed retrieval over claim text, applicability conditions and topic names.
-- Additive: the original memory_text analyzer and memory_search index remain defined.
-- A second FULLTEXT index on `text` would be shadowed by memory_search, so the
-- stemmed index uses a derived field recomputed on every write.
DEFINE ANALYZER IF NOT EXISTS memory_stem TOKENIZERS blank,class,punct FILTERS lowercase,ascii,snowball(english);
DEFINE FIELD IF NOT EXISTS search_text ON memory TYPE string VALUE string::join(' ', text, conditions ?? '', array::join(topics ?? [], ' '));
DEFINE INDEX IF NOT EXISTS memory_search_stem ON memory FIELDS search_text FULLTEXT ANALYZER memory_stem BM25;
-- Idempotent backfill of the derived field for records written before it existed.
UPDATE memory WHERE search_text = NONE;
-- Readable label for browsers and results (the record ID is a hash). Additive and optional.
DEFINE FIELD IF NOT EXISTS title ON memory TYPE option<string>;
-- Semantic vectors live beside, not inside, memory so records stay readable. `model` names the
-- embedding space; only vectors with the same model are compared.
DEFINE TABLE IF NOT EXISTS memory_embedding SCHEMALESS COMMENT 'Derived vectors; safe to rebuild from memory text.';
DEFINE FIELD IF NOT EXISTS memory ON memory_embedding TYPE record<memory>;
DEFINE FIELD IF NOT EXISTS model ON memory_embedding TYPE string;
DEFINE FIELD IF NOT EXISTS vector ON memory_embedding TYPE array<float>;
DEFINE INDEX IF NOT EXISTS memory_embedding_memory ON memory_embedding FIELDS memory UNIQUE;
-- Which memories searches return, for usage statistics. Stores no query text.
DEFINE TABLE IF NOT EXISTS retrieval SCHEMALESS COMMENT 'Search result log: who searched, mode and returned memory IDs; never the query.';
-- Derived "close in meaning" links between memories, recomputed by `memoryctl.py link-similar`.
DEFINE TABLE IF NOT EXISTS similar_to TYPE RELATION IN memory OUT memory COMMENT 'Derived from embeddings; safe to rebuild.';
DEFINE FIELD IF NOT EXISTS similarity ON similar_to TYPE float;
