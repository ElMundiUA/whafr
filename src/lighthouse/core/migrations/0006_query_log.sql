-- 0006_query_log — per-search analytics log + coverage-gap triage state.
--
-- Every /v1/search (and the MCP search tool, which goes through the same
-- route) appends one row here, fire-and-forget — a failed insert never
-- fails the search. The admin UI's Dashboard / Top questions / Coverage
-- gaps / Source analytics pages aggregate over this table.
--
-- `gap` marks searches that returned zero hits: the caller asked
-- something the corpus can't answer. Grouped by normalized query text
-- these become coverage-gap clusters; `coverage_gap_status` holds the
-- librarian's triage state per cluster (kapa-style status tags).

CREATE TABLE IF NOT EXISTS query_log (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id TEXT NOT NULL DEFAULT 'public',
    query        TEXT NOT NULL,
    top_k        INT  NOT NULL DEFAULT 10,
    hit_count    INT  NOT NULL DEFAULT 0,
    -- distinct `source` values of the returned hits, in rank order —
    -- powers "which sources actually answer questions" analytics.
    top_sources  TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    latency_ms   INT,
    gap          BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS query_log_ws_created_idx
    ON query_log (workspace_id, created_at DESC);

CREATE INDEX IF NOT EXISTS query_log_gap_idx
    ON query_log (workspace_id, created_at DESC)
    WHERE gap;

CREATE TABLE IF NOT EXISTS coverage_gap_status (
    workspace_id TEXT NOT NULL,
    -- lower(btrim(query)) — same normalization the analytics GROUP BY uses.
    query_norm   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open'
                   CHECK (status IN ('open', 'planned', 'resolved', 'ignored')),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (workspace_id, query_norm)
);
