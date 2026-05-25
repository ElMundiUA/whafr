-- 0004_importer_workspace_name_unique — names are unique *per workspace*.
--
-- Makes per-workspace provisioning idempotent (get-or-create by name)
-- and prevents two importers with the same name inside one tenant. This
-- replaces the SaaS web/sql global ``importers(name)`` unique index,
-- which is wrong for a multi-tenant instance (two workspaces must each
-- be able to have a "Workspace knowledge (S3)" importer).

CREATE UNIQUE INDEX IF NOT EXISTS importers_workspace_name_unique
    ON importers (workspace_id, name);
