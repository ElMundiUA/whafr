"""/v1/webhooks — register/manage outgoing webhook subscriptions.

A subscription holds:
- `url` — the receiver
- `secret` — HMAC-SHA256 key used to sign every delivery body
- `events` — list of event names this URL wants. `*` = all.

On every emit (see `lighthouse.webhooks.emit_event`), a row gets
inserted in `webhook_deliveries`; the worker drains them.

Auth: same shared bearer (`LIGHTHOUSE_ADMIN_TOKEN`) as the rest of
`/v1` admin surface.
"""

from __future__ import annotations

import json
import secrets as pysecrets
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lighthouse.api.dependencies import get_pg_pool, get_workspace, require_admin

router = APIRouter(
    prefix="/v1/webhooks",
    tags=["v1", "webhooks"],
    dependencies=[Depends(require_admin)],
)


# ───────────────────────── Schemas ─────────────────────────


class WebhookOut(BaseModel):
    id: UUID
    url: str
    events: list[str]
    enabled: bool
    description: str | None
    created_at: datetime
    last_delivery_at: datetime | None
    last_status: int | None
    last_error: str | None


class WebhookCreate(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    events: list[str] = Field(default_factory=lambda: ["*"])
    description: str | None = None
    # When omitted we generate a random 32-byte secret and return it
    # ONCE in the response — callers must store it because the GET
    # endpoint redacts the field forever after.
    secret: str | None = None
    enabled: bool = True


class WebhookCreated(WebhookOut):
    secret: str  # only echoed on create + rotate


class WebhookUpdate(BaseModel):
    url: str | None = Field(default=None, min_length=1, max_length=2000)
    events: list[str] | None = None
    description: str | None = None
    enabled: bool | None = None
    rotate_secret: bool = False


class DeliveryOut(BaseModel):
    id: UUID
    webhook_id: UUID
    event: str
    status: str
    attempts: int
    next_attempt_at: datetime
    last_status: int | None
    last_error: str | None
    created_at: datetime
    delivered_at: datetime | None


# ───────────────────────── Routes ──────────────────────────


def _to_out(row: asyncpg.Record) -> WebhookOut:
    return WebhookOut(
        id=row["id"],
        url=row["url"],
        events=list(row["events"] or []),
        enabled=row["enabled"],
        description=row["description"],
        created_at=row["created_at"],
        last_delivery_at=row["last_delivery_at"],
        last_status=row["last_status"],
        last_error=row["last_error"],
    )


@router.get("/", response_model=list[WebhookOut])
async def list_all(
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
    workspace_id: Annotated[str, Depends(get_workspace)],
) -> list[WebhookOut]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, url, events, enabled, description, created_at,
                   last_delivery_at, last_status, last_error
              FROM webhooks
             WHERE workspace_id = $1
             ORDER BY created_at DESC
            """,
            workspace_id,
        )
    return [_to_out(r) for r in rows]


@router.post("/", response_model=WebhookCreated, status_code=201)
async def create(
    body: WebhookCreate,
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
    workspace_id: Annotated[str, Depends(get_workspace)],
) -> WebhookCreated:
    secret_val = body.secret or pysecrets.token_urlsafe(32)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO webhooks (url, secret, events, description, enabled,
                                  workspace_id)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, url, events, enabled, description, created_at,
                      last_delivery_at, last_status, last_error
            """,
            body.url,
            secret_val,
            body.events,
            body.description,
            body.enabled,
            workspace_id,
        )
    assert row is not None
    base = _to_out(row)
    return WebhookCreated(**base.model_dump(), secret=secret_val)


@router.get("/{webhook_id}", response_model=WebhookOut)
async def get(
    webhook_id: UUID,
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
    workspace_id: Annotated[str, Depends(get_workspace)],
) -> WebhookOut:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, url, events, enabled, description, created_at,
                   last_delivery_at, last_status, last_error
              FROM webhooks WHERE id = $1 AND workspace_id = $2
            """,
            webhook_id,
            workspace_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return _to_out(row)


@router.patch("/{webhook_id}", response_model=WebhookOut | WebhookCreated)  # type: ignore[arg-type]
async def update(
    webhook_id: UUID,
    body: WebhookUpdate,
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
    workspace_id: Annotated[str, Depends(get_workspace)],
) -> Any:
    sets: list[str] = []
    args: list[Any] = []
    if body.url is not None:
        sets.append(f"url = ${len(args) + 1}")
        args.append(body.url)
    if body.events is not None:
        sets.append(f"events = ${len(args) + 1}")
        args.append(body.events)
    if body.description is not None:
        sets.append(f"description = ${len(args) + 1}")
        args.append(body.description)
    if body.enabled is not None:
        sets.append(f"enabled = ${len(args) + 1}")
        args.append(body.enabled)
    new_secret: str | None = None
    if body.rotate_secret:
        new_secret = pysecrets.token_urlsafe(32)
        sets.append(f"secret = ${len(args) + 1}")
        args.append(new_secret)
    if not sets:
        return await get(webhook_id, pool, workspace_id)
    args.append(webhook_id)
    args.append(workspace_id)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE webhooks SET {", ".join(sets)}
             WHERE id = ${len(args) - 1} AND workspace_id = ${len(args)}
            RETURNING id, url, events, enabled, description, created_at,
                      last_delivery_at, last_status, last_error
            """,
            *args,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    out = _to_out(row)
    if new_secret:
        return WebhookCreated(**out.model_dump(), secret=new_secret)
    return out


@router.delete("/{webhook_id}", status_code=204)
async def delete(
    webhook_id: UUID,
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
    workspace_id: Annotated[str, Depends(get_workspace)],
) -> None:
    async with pool.acquire() as conn:
        r = await conn.execute(
            "DELETE FROM webhooks WHERE id = $1 AND workspace_id = $2",
            webhook_id,
            workspace_id,
        )
    if not r.endswith(" 1"):
        raise HTTPException(status_code=404, detail="not found")


@router.get("/{webhook_id}/deliveries", response_model=list[DeliveryOut])
async def deliveries(
    webhook_id: UUID,
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
    workspace_id: Annotated[str, Depends(get_workspace)],
    limit: int = 50,
) -> list[DeliveryOut]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT d.id, d.webhook_id, d.event, d.status, d.attempts,
                   d.next_attempt_at, d.last_status, d.last_error,
                   d.created_at, d.delivered_at
              FROM webhook_deliveries d
              JOIN webhooks w ON w.id = d.webhook_id
             WHERE d.webhook_id = $1 AND w.workspace_id = $2
             ORDER BY d.created_at DESC
             LIMIT $3
            """,
            webhook_id,
            workspace_id,
            limit,
        )
    return [DeliveryOut(**dict(r)) for r in rows]


@router.post("/{webhook_id}/deliveries/{delivery_id}/redeliver", status_code=202)
async def redeliver(
    webhook_id: UUID,
    delivery_id: UUID,
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
    workspace_id: Annotated[str, Depends(get_workspace)],
) -> dict[str, str]:
    """Reset a delivery row to `pending` + next_attempt_at=NOW. The
    worker picks it up on the next tick."""
    async with pool.acquire() as conn:
        r = await conn.execute(
            """
            UPDATE webhook_deliveries d
               SET status = 'pending', next_attempt_at = NOW(),
                   attempts = 0, last_error = NULL
              FROM webhooks w
             WHERE d.id = $1 AND d.webhook_id = $2
               AND w.id = d.webhook_id AND w.workspace_id = $3
            """,
            delivery_id,
            webhook_id,
            workspace_id,
        )
    if not r.endswith(" 1"):
        raise HTTPException(status_code=404, detail="delivery not found")
    return {"status": "requeued"}


@router.post("/{webhook_id}/deliveries/requeue-dead")
async def requeue_dead(
    webhook_id: UUID,
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
    workspace_id: Annotated[str, Depends(get_workspace)],
) -> dict[str, int]:
    """Bulk-requeue every `dead` delivery of this webhook (attempts
    exhausted). Resets the retry counter so the worker walks the full
    backoff curve again — use after fixing the receiver."""
    async with pool.acquire() as conn:
        r = await conn.execute(
            """
            UPDATE webhook_deliveries d
               SET status = 'pending', next_attempt_at = NOW(),
                   attempts = 0, last_error = NULL
              FROM webhooks w
             WHERE d.webhook_id = $1 AND d.status = 'dead'
               AND w.id = d.webhook_id AND w.workspace_id = $2
            """,
            webhook_id,
            workspace_id,
        )
    # asyncpg returns e.g. "UPDATE 3".
    return {"requeued": int(r.split()[-1])}


@router.post("/{webhook_id}/test", status_code=202)
async def test(
    webhook_id: UUID,
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
    workspace_id: Annotated[str, Depends(get_workspace)],
) -> dict[str, Any]:
    """Enqueue a synthetic `ping` event for this webhook only.

    Useful for first-time setup — verifies URL + signing + receiver
    parsing without waiting for a real importer run."""
    async with pool.acquire() as conn:
        wh = await conn.fetchrow(
            "SELECT id FROM webhooks WHERE id = $1 AND workspace_id = $2",
            webhook_id,
            workspace_id,
        )
        if wh is None:
            raise HTTPException(status_code=404, detail="not found")
        body = {
            "event": "ping",
            "ts": datetime.utcnow().isoformat() + "Z",
            "data": {"message": "Lighthouse webhook test"},
        }
        d_row = await conn.fetchrow(
            """
            INSERT INTO webhook_deliveries
              (webhook_id, event, payload, status, next_attempt_at,
               workspace_id)
            VALUES ($1, 'ping', $2::jsonb, 'pending', NOW(), $3)
            RETURNING id
            """,
            webhook_id,
            json.dumps(body),
            workspace_id,
        )
    assert d_row is not None
    return {"delivery_id": str(d_row["id"])}
