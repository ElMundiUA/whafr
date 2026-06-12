-- 0010_workspaces — workspace registry + per-workspace auth policy.
--
-- Fixes the mixed-mode footgun the scenario suite surfaced: with the
-- instance-wide LIGHTHOUSE_RETRIEVAL_AUTH_REQUIRED off (default), a
-- keyless caller could read ANY workspace by guessing its id — even in
-- deployments where other teams use API keys. `require_auth = TRUE`
-- locks a single workspace to key-holders while the rest of the
-- instance (e.g. a public corpus in 'public') stays open.
--
-- The table doubles as the workspace registry the admin UI lists —
-- previously tenants existed only as scattered workspace_id strings.

CREATE TABLE IF NOT EXISTS workspaces (
    id           TEXT PRIMARY KEY,
    require_auth BOOLEAN NOT NULL DEFAULT FALSE,
    description  TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
