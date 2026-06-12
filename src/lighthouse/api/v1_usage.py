"""/v1/usage — billing-shaped usage rollups over ``query_log``.

The metering surface a hosted offering (or an org's chargeback) reads:
how many searches a workspace ran in the window, attributed per API
key (keyless legacy traffic shows up as the ``null``-key row) and per
day. Same admin bearer as the rest of /v1; scoped to ``X-Workspace``.

This is read-side only — quota *enforcement* stays a deliberate
non-feature until plans exist; the numbers here are what a control
plane would bill from.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from lighthouse.api.dependencies import get_pg_pool, get_workspace, require_admin

router = APIRouter(
    prefix="/v1/usage",
    tags=["v1", "usage"],
    dependencies=[Depends(require_admin)],
)


class KeyUsage(BaseModel):
    api_key_id: UUID | None
    """None = keyless legacy traffic."""
    key_name: str | None
    searches: int
    gaps: int
    last_used_at: datetime


class DayUsage(BaseModel):
    day: datetime
    searches: int


class UsageOut(BaseModel):
    workspace_id: str
    days: int
    total_searches: int
    by_key: list[KeyUsage]
    by_day: list[DayUsage]


@router.get("/", response_model=UsageOut)
async def usage(
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
    workspace_id: Annotated[str, Depends(get_workspace)],
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> UsageOut:
    async with pool.acquire() as conn:
        by_key = await conn.fetch(
            """
            SELECT q.api_key_id,
                   k.name                              AS key_name,
                   COUNT(*)::int                       AS searches,
                   COUNT(*) FILTER (WHERE q.gap)::int  AS gaps,
                   MAX(q.created_at)                   AS last_used_at
              FROM query_log q
              LEFT JOIN api_keys k ON k.id = q.api_key_id
             WHERE q.workspace_id = $1
               AND q.created_at >= NOW() - make_interval(days => $2)
             GROUP BY q.api_key_id, k.name
             ORDER BY searches DESC
            """,
            workspace_id,
            days,
        )
        by_day = await conn.fetch(
            """
            SELECT date_trunc('day', created_at) AS day,
                   COUNT(*)::int                 AS searches
              FROM query_log
             WHERE workspace_id = $1
               AND created_at >= NOW() - make_interval(days => $2)
             GROUP BY 1
             ORDER BY 1
            """,
            workspace_id,
            days,
        )
    return UsageOut(
        workspace_id=workspace_id,
        days=days,
        total_searches=sum(r["searches"] for r in by_key),
        by_key=[
            KeyUsage(
                api_key_id=r["api_key_id"],
                key_name=r["key_name"],
                searches=r["searches"],
                gaps=r["gaps"],
                last_used_at=r["last_used_at"],
            )
            for r in by_key
        ],
        by_day=[
            DayUsage(day=r["day"], searches=r["searches"]) for r in by_day
        ],
    )
