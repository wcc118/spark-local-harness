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
