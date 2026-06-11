-- 0009_webhooks_workspace — tenant isolation for webhooks.
--
-- The 0005 schema had no workspace column: any workspace could
-- subscribe a URL and receive every other workspace's importer.run.*
-- events (data leakage about sources, timing, volumes). Existing rows
-- are grandfathered into the reserved 'public' workspace.

ALTER TABLE webhooks
    ADD COLUMN IF NOT EXISTS workspace_id TEXT NOT NULL DEFAULT 'public';
ALTER TABLE webhook_deliveries
    ADD COLUMN IF NOT EXISTS workspace_id TEXT NOT NULL DEFAULT 'public';

CREATE INDEX IF NOT EXISTS webhooks_workspace_idx
    ON webhooks (workspace_id, created_at DESC);
