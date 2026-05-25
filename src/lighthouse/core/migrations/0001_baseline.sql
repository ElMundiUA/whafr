-- 0001_baseline — current flat (pgvector) schema, captured verbatim.
--
-- Every statement is idempotent (IF NOT EXISTS) so on an existing prod
-- DB this whole migration is a no-op and on a fresh DB it builds the
-- exact same schema the old in-code initialize() produced. Do NOT
-- "improve" the baseline (e.g. the known keywords-not-in-tsv_boosted
-- drift) — capture current reality; ship fixes as later migrations.
--
-- __EMBEDDING_DIM__ is substituted with settings.openai_embedding_dim
-- by the migration runner before execution.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS chunks (
    uuid             UUID PRIMARY KEY,
    name             TEXT,
    source           TEXT NOT NULL,
    url              TEXT,
    content          TEXT NOT NULL,
    content_sha256   TEXT NOT NULL,
    full_body_sha256 TEXT,
    published_at     TIMESTAMPTZ,
    ingested_at      TIMESTAMPTZ DEFAULT now(),
    version          TEXT,
    superseded_by    UUID REFERENCES chunks(uuid) ON DELETE SET NULL,
    chunk_index      INTEGER,
    chunk_count      INTEGER,
    embedding        vector(__EMBEDDING_DIM__),
    summary          TEXT,
    tags             TEXT,
    tsv              tsvector GENERATED ALWAYS AS (
                         setweight(to_tsvector('english', coalesce(name,'')), 'A')
                         || setweight(to_tsvector('english', coalesce(content,'')), 'B')
                     ) STORED,
    tsv_boosted      tsvector GENERATED ALWAYS AS (
                         setweight(to_tsvector('english', coalesce(summary,'')), 'A')
                         || setweight(to_tsvector('english', coalesce(tags,'')), 'A')
                         || setweight(to_tsvector('english', coalesce(name,'')), 'B')
                         || setweight(to_tsvector('english', coalesce(content,'')), 'C')
                     ) STORED
);

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS summary TEXT;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS tags TEXT;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS keywords TEXT;
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS recipes TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[];

CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON chunks USING GIN (tsv);
CREATE INDEX IF NOT EXISTS chunks_tsv_boosted_gin ON chunks USING GIN (tsv_boosted);
CREATE INDEX IF NOT EXISTS chunks_source_published_idx ON chunks (source, published_at DESC);
CREATE INDEX IF NOT EXISTS chunks_full_body_sha_idx ON chunks (full_body_sha256);
CREATE INDEX IF NOT EXISTS chunks_published_at_idx ON chunks (published_at DESC) WHERE superseded_by IS NULL;
CREATE INDEX IF NOT EXISTS chunks_recipes_gin ON chunks USING GIN (recipes);
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_idx ON chunks USING hnsw (embedding vector_cosine_ops);
