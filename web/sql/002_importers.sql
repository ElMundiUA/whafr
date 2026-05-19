-- E27 — admin-managed importers (engine-side feature).
--
-- An "importer" is a saved instance of a registered importer type
-- (web_pages, sitemap, github_repo, …) with a user-provided config
-- and optional encrypted secrets. Engine operators add importers
-- through the admin UI; the Python runner picks them up, builds a
-- Connector, drains it into the same chunks table the legacy YAML
-- pipeline writes to.
--
-- Notes:
-- - `type` is the registry key; the Python side validates that the
--   value exists in lighthouse.importers.registry.
-- - `config` is non-secret JSON (URLs, sitemap roots, branch names,
--   include/exclude patterns, role tag).
-- - `secrets_enc` holds a Fernet-encrypted JSON blob with everything
--   the importer flagged as secret (PATs, OAuth tokens, S3 keys).
--   Master key lives in LIGHTHOUSE_SECRETS_KEY env var; without it
--   the API refuses to read or write secret-bearing importers.
-- - `recipe` is the slug stamped on every chunk this importer
--   produces (so the existing top-level corpus filter just works).

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
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS importers_recipe_idx
    ON importers (recipe);

CREATE INDEX IF NOT EXISTS importers_last_run_idx
    ON importers (last_run_at DESC NULLS LAST);

-- History of executions. One row per .run() invocation. `items_*` /
-- `chunks_added` are best-effort counters the runner increments; on
-- crash the row stays in 'running' and the runner cleans up on next
-- start.
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

CREATE INDEX IF NOT EXISTS importer_runs_importer_idx
    ON importer_runs (importer_id, started_at DESC);
