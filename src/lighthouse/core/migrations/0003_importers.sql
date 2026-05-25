-- 0003_importers — bring the importers schema into the engine's
-- migration runner so a fresh engine-only instance (e.g. Ship's
-- per-workspace deployment) is self-sufficient: just whafr + Postgres,
-- no dependency on the lighthouse-saas web layer.
--
-- The CREATE TABLEs match lighthouse-saas web/sql/002_importers.sql, so
-- on a DB the SaaS already provisioned they are no-ops. The trailing
-- ALTER adds row-level tenancy (workspace_id) — the importer's
-- workspace_id is stamped onto every chunk it produces (run_importer →
-- drain), so a per-workspace importer's output lands in that
-- workspace's slice. Existing importers backfill to 'public'.

CREATE TABLE IF NOT EXISTS importers (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type            TEXT NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT,
    recipe          TEXT NOT NULL,
    config          JSONB NOT NULL DEFAULT '{}'::jsonb,
    secrets_enc     BYTEA,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    status          TEXT NOT NULL DEFAULT 'idle'
                       CHECK (status IN ('idle', 'queued', 'running', 'error')),
    last_run_at     TIMESTAMPTZ,
    last_error      TEXT,
    created_by      TEXT,
    workspace_id    TEXT NOT NULL DEFAULT 'public',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS importer_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    importer_id     UUID NOT NULL REFERENCES importers(id) ON DELETE CASCADE,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    status          TEXT NOT NULL
                       CHECK (status IN ('running', 'success', 'error', 'cancelled')),
    items_total     INTEGER,
    items_done      INTEGER NOT NULL DEFAULT 0,
    chunks_added    INTEGER NOT NULL DEFAULT 0,
    error_text      TEXT,
    triggered_by    TEXT
);

-- Patch DBs provisioned by the SaaS web layer before this column existed.
ALTER TABLE importers ADD COLUMN IF NOT EXISTS workspace_id TEXT NOT NULL DEFAULT 'public';

CREATE INDEX IF NOT EXISTS importers_recipe_idx ON importers (recipe);
CREATE INDEX IF NOT EXISTS importers_last_run_idx
    ON importers (last_run_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS importers_workspace_idx ON importers (workspace_id);
CREATE INDEX IF NOT EXISTS importer_runs_importer_idx
    ON importer_runs (importer_id, started_at DESC);
