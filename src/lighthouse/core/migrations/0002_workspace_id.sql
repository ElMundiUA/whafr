-- 0002_workspace_id — row-level multi-tenancy on chunks.
--
-- Every chunk belongs to exactly one workspace. The existing single-
-- tenant corpus (harborgang public site) backfills to 'public' via the
-- column default, so it keeps working unchanged. All reads/writes must
-- filter/set workspace_id (enforced in the FlatGraph layer, K2).

ALTER TABLE chunks ADD COLUMN IF NOT EXISTS workspace_id TEXT NOT NULL DEFAULT 'public';

-- Plain btree for equality filtering; the planner ANDs it with the
-- GIN/HNSW indexes on the search path.
CREATE INDEX IF NOT EXISTS chunks_workspace_idx ON chunks (workspace_id);

-- Composite for the common "recent, live, this workspace" scans.
CREATE INDEX IF NOT EXISTS chunks_workspace_published_idx
    ON chunks (workspace_id, published_at DESC)
    WHERE superseded_by IS NULL;
