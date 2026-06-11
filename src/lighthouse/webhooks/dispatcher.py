"""Emit + deliver webhook events.

`emit_event` is the producer side: fans a single event out to
`webhook_deliveries` rows, one per matching subscription. The
producer is fire-and-forget — failure to enqueue is logged but never
blocks the calling code path (e.g. importer-run completion).

`run_worker` is the consumer: long-running asyncio task spawned from
the API lifespan that polls the queue, POSTs with HMAC, marks the
row delivered/failed/dead. Exponential backoff on failure:
delays ~ 30s, 2min, 10min, 1hr, then dead.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import httpx

from lighthouse.webhooks.signing import sign_payload

logger = logging.getLogger(__name__)

# Backoff curve (seconds) — keep short enough that a flaky receiver
# recovers within the day, long enough not to hammer.
_BACKOFFS = (30, 120, 600, 3600)
MAX_ATTEMPTS = len(_BACKOFFS) + 1


async def emit_event(
    pool: asyncpg.Pool,
    event: str,
    payload: dict[str, Any],
    *,
    workspace_id: str = "public",
) -> int:
    """Enqueue `event` for every webhook subscribed IN THIS WORKSPACE.

    Tenant isolation happens here: a webhook only ever receives events
    from its own workspace (0009 added the column; pre-existing rows
    live in 'public'). Returns the number of delivery rows inserted.
    Idempotent at the schema level — duplicate calls just create
    duplicate deliveries; consumers should be event-id-tolerant.
    """
    body = {
        "event": event,
        "ts": datetime.now(UTC).isoformat(),
        "workspace_id": workspace_id,
        "data": payload,
    }
    body_json = json.dumps(body)
    n = 0
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id FROM webhooks
             WHERE enabled = TRUE
               AND workspace_id = $2
               AND ($1 = ANY(events) OR '*' = ANY(events))
            """,
            event,
            workspace_id,
        )
        for r in rows:
            await conn.execute(
                """
                INSERT INTO webhook_deliveries
                  (webhook_id, event, payload, status, next_attempt_at,
                   workspace_id)
                VALUES ($1, $2, $3::jsonb, 'pending', NOW(), $4)
                """,
                r["id"],
                event,
                body_json,
                workspace_id,
            )
            n += 1
    if n:
        logger.info("emitted %s → %d deliveries", event, n)
    return n


async def _attempt_once(
    pool: asyncpg.Pool, row: asyncpg.Record, client: httpx.AsyncClient
) -> None:
    """Try to deliver one row. Updates the row in-place on success/fail."""
    body_str: str = (
        row["payload"]
        if isinstance(row["payload"], str)
        else json.dumps(row["payload"], default=str)
    )
    body = body_str.encode("utf-8")
    sig = sign_payload(row["secret"], body)
    try:
        r = await client.post(
            row["url"],
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Lighthouse-Signature": sig,
                "X-Lighthouse-Event": row["event"],
                "X-Lighthouse-Delivery": str(row["id"]),
            },
            timeout=10.0,
        )
        status = r.status_code
        response_text = r.text[:2000]
        ok = 200 <= status < 300
        async with pool.acquire() as conn:
            if ok:
                await conn.execute(
                    """
                    UPDATE webhook_deliveries
                       SET status = 'delivered', delivered_at = NOW(),
                           attempts = attempts + 1, last_status = $1,
                           last_response = $2, last_error = NULL
                     WHERE id = $3
                    """,
                    status,
                    response_text,
                    row["id"],
                )
                await conn.execute(
                    """
                    UPDATE webhooks
                       SET last_delivery_at = NOW(), last_status = $1,
                           last_error = NULL
                     WHERE id = $2
                    """,
                    status,
                    row["webhook_id"],
                )
            else:
                await _mark_failure(
                    conn,
                    row,
                    last_status=status,
                    last_response=response_text,
                    last_error=f"HTTP {status}",
                )
    except (httpx.HTTPError, OSError) as exc:
        async with pool.acquire() as conn:
            await _mark_failure(
                conn,
                row,
                last_status=None,
                last_response=None,
                last_error=str(exc)[:1000],
            )


async def _mark_failure(
    conn: asyncpg.Connection,
    row: asyncpg.Record,
    *,
    last_status: int | None,
    last_response: str | None,
    last_error: str,
) -> None:
    attempts = row["attempts"] + 1
    if attempts >= MAX_ATTEMPTS:
        await conn.execute(
            """
            UPDATE webhook_deliveries
               SET status = 'dead', attempts = $1, last_status = $2,
                   last_response = $3, last_error = $4
             WHERE id = $5
            """,
            attempts,
            last_status,
            last_response,
            last_error,
            row["id"],
        )
        return
    delay = _BACKOFFS[min(attempts - 1, len(_BACKOFFS) - 1)]
    next_at = datetime.now(UTC) + timedelta(seconds=delay)
    await conn.execute(
        """
        UPDATE webhook_deliveries
           SET status = 'failed', attempts = $1,
               next_attempt_at = $2, last_status = $3,
               last_response = $4, last_error = $5
         WHERE id = $6
        """,
        attempts,
        next_at,
        last_status,
        last_response,
        last_error,
        row["id"],
    )


async def _claim_batch(pool: asyncpg.Pool, batch: int) -> list[asyncpg.Record]:
    """Lock + fetch the next batch of deliveries due now.

    Uses SELECT … FOR UPDATE SKIP LOCKED so multiple workers don't
    fight over the same row. Each worker pulls a chunk, marks
    'pending' as nothing to skip — the row's current status is
    `pending`/`failed`, and the worker updates after the POST."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT d.id, d.webhook_id, d.event, d.payload, d.attempts,
                       w.url, w.secret
                  FROM webhook_deliveries d
                  JOIN webhooks w ON w.id = d.webhook_id
                 WHERE d.status IN ('pending', 'failed')
                   AND w.enabled = TRUE
                   AND d.next_attempt_at <= NOW()
                 ORDER BY d.next_attempt_at
                  FOR UPDATE OF d SKIP LOCKED
                 LIMIT $1
                """,
                batch,
            )
        return list(rows)


async def run_worker(pool: asyncpg.Pool, *, poll_interval: float = 5.0) -> None:
    """Long-lived task: drain the delivery queue.

    Started from `main.py`'s lifespan; cancelled on shutdown. Keeps a
    single httpx.AsyncClient open across iterations (connection reuse
    matters for chatty subscribers)."""
    logger.info("webhook worker started (poll=%ss)", poll_interval)
    async with httpx.AsyncClient() as client:
        while True:
            try:
                rows = await _claim_batch(pool, batch=10)
                if rows:
                    await asyncio.gather(
                        *(_attempt_once(pool, r, client) for r in rows),
                        return_exceptions=True,
                    )
                    continue
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("webhook worker tick failed")
            try:
                await asyncio.sleep(poll_interval)
            except asyncio.CancelledError:
                raise
