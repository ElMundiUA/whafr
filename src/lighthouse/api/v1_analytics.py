"""/v1/analytics/* — search-traffic analytics over ``query_log``.

Backs the admin UI's Dashboard, Top questions, Coverage gaps and
Source analytics pages (kapa.ai-style). All aggregates are scoped to
the caller's workspace and a trailing ``days`` window.

Endpoints:

- ``GET  /overview``      → totals + per-day questions/gaps timeseries
- ``GET  /top-queries``   → most-asked queries (normalized), gap share
- ``GET  /gaps``          → zero-hit query clusters + triage status
- ``PATCH /gaps/status``  → set a cluster's triage status
- ``GET  /source-usage``  → which sources actually appear in results

Auth: same shared bearer (``LIGHTHOUSE_ADMIN_TOKEN``) as the rest of
the ``/v1`` admin surface.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

import asyncpg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from lighthouse.api.dependencies import get_pg_pool, get_workspace, require_admin

router = APIRouter(
    prefix="/v1/analytics",
    tags=["v1", "analytics"],
    dependencies=[Depends(require_admin)],
)


# ───────────────────────── Schemas ─────────────────────────


class DayPoint(BaseModel):
    day: datetime
    questions: int
    gaps: int


class OverviewOut(BaseModel):
    days: int
    total_questions: int
    total_gaps: int
    gap_rate: float
    """Share of searches flagged as gaps — zero hits, or hits the
    usefulness classifier rated below threshold (0.0–1.0)."""
    total_uncertain: int
    """Gaps that DID return hits but were rated not useful enough
    ("uncertain answers"). 0 unless the gap classifier is enabled."""
    avg_useful_score: float | None
    """Mean 1-5 usefulness across classified searches; None when the
    classifier is off."""
    avg_latency_ms: float | None
    timeseries: list[DayPoint]


class TopQueryOut(BaseModel):
    query: str
    """Normalized (lower/trimmed) query text the cluster groups on."""
    count: int
    gap_count: int
    avg_hits: float
    last_asked_at: datetime


class GapOut(BaseModel):
    query: str
    count: int
    last_asked_at: datetime
    status: str = "open"


class GapStatusIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    status: Literal["open", "planned", "resolved", "ignored"]


class SourceUsageOut(BaseModel):
    source: str
    references: int
    """How many searches in the window surfaced this source in results."""
    last_referenced_at: datetime


# ───────────────────────── Routes ──────────────────────────


@router.get("/overview", response_model=OverviewOut)
async def overview(
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
    workspace_id: Annotated[str, Depends(get_workspace)],
    days: Annotated[int, Query(ge=1, le=365)] = 30,
) -> OverviewOut:
    async with pool.acquire() as conn:
        totals = await conn.fetchrow(
            """
            SELECT COUNT(*)::int                       AS total_questions,
                   COUNT(*) FILTER (WHERE gap)::int    AS total_gaps,
                   COUNT(*) FILTER (WHERE gap AND hit_count > 0)::int
                                                       AS total_uncertain,
                   AVG(useful_score)::float            AS avg_useful_score,
                   AVG(latency_ms)::float              AS avg_latency_ms
              FROM query_log
             WHERE workspace_id = $1
               AND created_at >= NOW() - make_interval(days => $2)
            """,
            workspace_id,
            days,
        )
        series = await conn.fetch(
            """
            SELECT date_trunc('day', created_at)       AS day,
                   COUNT(*)::int                       AS questions,
                   COUNT(*) FILTER (WHERE gap)::int    AS gaps
              FROM query_log
             WHERE workspace_id = $1
               AND created_at >= NOW() - make_interval(days => $2)
             GROUP BY 1
             ORDER BY 1
            """,
            workspace_id,
            days,
        )
    assert totals is not None
    total_q = totals["total_questions"] or 0
    total_g = totals["total_gaps"] or 0
    return OverviewOut(
        days=days,
        total_questions=total_q,
        total_gaps=total_g,
        gap_rate=(total_g / total_q) if total_q else 0.0,
        total_uncertain=totals["total_uncertain"] or 0,
        avg_useful_score=totals["avg_useful_score"],
        avg_latency_ms=totals["avg_latency_ms"],
        timeseries=[
            DayPoint(day=r["day"], questions=r["questions"], gaps=r["gaps"])
            for r in series
        ],
    )


@router.get("/top-queries", response_model=list[TopQueryOut])
async def top_queries(
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
    workspace_id: Annotated[str, Depends(get_workspace)],
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[TopQueryOut]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT lower(btrim(query))                 AS query_norm,
                   COUNT(*)::int                       AS count,
                   COUNT(*) FILTER (WHERE gap)::int    AS gap_count,
                   AVG(hit_count)::float               AS avg_hits,
                   MAX(created_at)                     AS last_asked_at
              FROM query_log
             WHERE workspace_id = $1
               AND created_at >= NOW() - make_interval(days => $2)
             GROUP BY 1
             ORDER BY count DESC, last_asked_at DESC
             LIMIT $3
            """,
            workspace_id,
            days,
            limit,
        )
    return [
        TopQueryOut(
            query=r["query_norm"],
            count=r["count"],
            gap_count=r["gap_count"],
            avg_hits=r["avg_hits"] or 0.0,
            last_asked_at=r["last_asked_at"],
        )
        for r in rows
    ]


@router.get("/gaps", response_model=list[GapOut])
async def gaps(
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
    workspace_id: Annotated[str, Depends(get_workspace)],
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    include_resolved: bool = False,
) -> list[GapOut]:
    """Zero-hit query clusters — the corpus' coverage gaps. Resolved /
    ignored clusters are hidden unless ``include_resolved`` is set, so
    triaged gaps stop cluttering the dashboard."""
    status_filter = (
        "" if include_resolved
        else "AND COALESCE(s.status, 'open') IN ('open', 'planned')"
    )
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT q.query_norm,
                   q.count,
                   q.last_asked_at,
                   COALESCE(s.status, 'open') AS status
              FROM (
                    SELECT lower(btrim(query)) AS query_norm,
                           COUNT(*)::int       AS count,
                           MAX(created_at)     AS last_asked_at
                      FROM query_log
                     WHERE workspace_id = $1
                       AND gap
                       AND created_at >= NOW() - make_interval(days => $2)
                     GROUP BY 1
                   ) q
              LEFT JOIN coverage_gap_status s
                ON s.workspace_id = $1 AND s.query_norm = q.query_norm
             WHERE TRUE {status_filter}
             ORDER BY q.count DESC, q.last_asked_at DESC
             LIMIT $3
            """,
            workspace_id,
            days,
            limit,
        )
    return [
        GapOut(
            query=r["query_norm"],
            count=r["count"],
            last_asked_at=r["last_asked_at"],
            status=r["status"],
        )
        for r in rows
    ]


@router.patch("/gaps/status", response_model=GapOut)
async def set_gap_status(
    body: GapStatusIn,
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
    workspace_id: Annotated[str, Depends(get_workspace)],
) -> GapOut:
    query_norm = body.query.strip().lower()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO coverage_gap_status (workspace_id, query_norm, status)
            VALUES ($1, $2, $3)
            ON CONFLICT (workspace_id, query_norm)
            DO UPDATE SET status = $3, updated_at = NOW()
            """,
            workspace_id,
            query_norm,
            body.status,
        )
        stats = await conn.fetchrow(
            """
            SELECT COUNT(*)::int AS count, MAX(created_at) AS last_asked_at
              FROM query_log
             WHERE workspace_id = $1 AND lower(btrim(query)) = $2
            """,
            workspace_id,
            query_norm,
        )
    assert stats is not None
    return GapOut(
        query=query_norm,
        count=stats["count"] or 0,
        last_asked_at=stats["last_asked_at"] or datetime.now().astimezone(),
        status=body.status,
    )


@router.get("/source-usage", response_model=list[SourceUsageOut])
async def source_usage(
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
    workspace_id: Annotated[str, Depends(get_workspace)],
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[SourceUsageOut]:
    """References per source — how often each indexed source actually
    shows up in search results. Sources with many chunks but no
    references are dead weight; heavily-referenced ones deserve more
    ingest attention."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT src                  AS source,
                   COUNT(*)::int        AS refs,
                   MAX(created_at)      AS last_referenced_at
              FROM query_log, unnest(top_sources) AS src
             WHERE workspace_id = $1
               AND created_at >= NOW() - make_interval(days => $2)
             GROUP BY 1
             ORDER BY refs DESC, last_referenced_at DESC
             LIMIT $3
            """,
            workspace_id,
            days,
            limit,
        )
    return [
        SourceUsageOut(
            source=r["source"],
            references=r["refs"],
            last_referenced_at=r["last_referenced_at"],
        )
        for r in rows
    ]
