-- 0005_webhooks — webhook subscriptions + delivery queue, owned by the
-- engine migration runner (was previously only in the lighthouse-saas
-- web/sql layer). The engine's webhook dispatcher + emit_event require
-- these tables; importer runs emit `importer.run.*` events, so a missing
-- webhook_deliveries table broke every run. Bringing it into the engine
-- makes a fresh engine-only instance self-sufficient. Idempotent.

CREATE TABLE IF NOT EXISTS webhooks (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    url              TEXT NOT NULL,
    secret           TEXT NOT NULL,
    events           TEXT[] NOT NULL DEFAULT ARRAY['*']::TEXT[],
    enabled          BOOLEAN NOT NULL DEFAULT TRUE,
    description      TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_delivery_at TIMESTAMPTZ,
    last_status      INTEGER,
    last_error       TEXT
);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    webhook_id      UUID NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
    event           TEXT NOT NULL,
    payload         JSONB NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'delivered', 'failed', 'dead')),
    attempts        INT NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_status     INTEGER,
    last_response   TEXT,
    last_error      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivered_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS webhook_deliveries_pending_idx
    ON webhook_deliveries (next_attempt_at)
    WHERE status IN ('pending', 'failed');

CREATE INDEX IF NOT EXISTS webhook_deliveries_webhook_idx
    ON webhook_deliveries (webhook_id, created_at DESC);
