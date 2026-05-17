-- Users + billing + usage. Lives in the same Neon database as the
-- corpus (chunks table). Logical separation only — no schema split.

CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    auth0_sub       TEXT UNIQUE NOT NULL,
    email           TEXT UNIQUE NOT NULL,
    name            TEXT,
    picture         TEXT,
    tier            TEXT NOT NULL DEFAULT 'free' CHECK (tier IN ('free', 'pro')),
    paddle_customer_id   TEXT,
    paddle_subscription_id TEXT,
    pro_until       TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at   TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS users_paddle_sub_idx
    ON users (paddle_subscription_id) WHERE paddle_subscription_id IS NOT NULL;

-- One row per (subject, day). subject = "u:<user_id>" for signed-in,
-- "ip:<v4-or-v6>" for anon. count is bumped per /search call.
CREATE TABLE IF NOT EXISTS usage_daily (
    subject     TEXT NOT NULL,
    day         DATE NOT NULL,
    count       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (subject, day)
);

CREATE INDEX IF NOT EXISTS usage_daily_day_idx ON usage_daily (day);

-- Audit log of Paddle webhook events — receipt is durable even if
-- our user-state apply path crashes.
CREATE TABLE IF NOT EXISTS paddle_events (
    id              SERIAL PRIMARY KEY,
    event_id        TEXT UNIQUE NOT NULL,
    event_type      TEXT NOT NULL,
    payload         JSONB NOT NULL,
    processed_at    TIMESTAMPTZ,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
