-- Webhook subscriptions + delivery queue.
--
-- A subscription holds a URL, an HMAC secret, and the set of event
-- names it wants. On each emit, the dispatcher inserts one
-- `webhook_deliveries` row per matching subscription; a background
-- worker drains the queue (POSTs with X-Lighthouse-Signature) and
-- retries with exponential backoff up to N tries.

CREATE TABLE IF NOT EXISTS webhooks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    url             TEXT NOT NULL,
    secret          TEXT NOT NULL,
    events          TEXT[] NOT NULL DEFAULT ARRAY['*']::TEXT[],
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    description     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_delivery_at TIMESTAMPTZ,
    last_status     INTEGER,
    last_error      TEXT
);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    webhook_id      UUID NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
    event           TEXT NOT NULL,
    payload         JSONB NOT NULL,
    -- Delivery lifecycle:
    --   pending   — queued, hasn't been tried yet
    --   delivered — got 2xx
    --   failed    — non-2xx or network error; retried later
    --   dead      — too many retries, given up
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
