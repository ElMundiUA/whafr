-- Enforce that importer names are unique so the YAML-recipe migration
-- and any future bulk-import is naturally idempotent (ON CONFLICT).
-- Operators add importers through the UI which already prompts on
-- collision, so the constraint isn't a usability footgun.

CREATE UNIQUE INDEX IF NOT EXISTS importers_name_unique
    ON importers (name);
