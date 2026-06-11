-- 0008_api_keys — per-workspace API keys for the retrieval surface.
--
-- Until now the workspace was taken from the client-asserted
-- X-Workspace header with no verification: anyone who guessed a
-- workspace id could read its corpus. API keys bind a bearer secret
-- to exactly one workspace; when LIGHTHOUSE_RETRIEVAL_AUTH_REQUIRED
-- is on, retrieval requests must present one and the workspace comes
-- from the key, never the header.
--
-- Only a SHA-256 of the secret is stored; the plaintext (lh_<hex>) is
-- shown once at create time. `scopes` is forward-compatible — today
-- only 'read' is meaningful.

CREATE TABLE IF NOT EXISTS api_keys (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id TEXT NOT NULL,
    name         TEXT NOT NULL,
    key_hash     TEXT NOT NULL UNIQUE,
    scopes       TEXT[] NOT NULL DEFAULT ARRAY['read']::TEXT[],
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at TIMESTAMPTZ,
    revoked_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS api_keys_workspace_idx
    ON api_keys (workspace_id, created_at DESC);

-- Billing-grade attribution: which key issued each logged search.
ALTER TABLE query_log ADD COLUMN IF NOT EXISTS api_key_id UUID;
