"""/v1/corpus/* — introspection over the indexed knowledge.

Used by Ship's UI to render "knowledge dashboard" panels: how many
chunks, which sources, what was ingested most recently. None of the
endpoints expose raw chunk text — `/v1/search` and `/v1/fetch_source`
serve that, and they already exist at their public retrieval URLs
(aliased here under /v1 for SDK uniformity).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from lighthouse.api.dependencies import get_pg_pool

router = APIRouter(prefix="/v1/corpus", tags=["v1", "corpus"])


class CorpusStats(BaseModel):
    total_chunks: int
    total_sources: int
    total_recipes: int
    chunks_with_summary: int
    chunks_with_embedding: int
    last_ingest_at: datetime | None


class CorpusSource(BaseModel):
    source: str
    chunk_count: int
    recipes: list[str]
    last_ingest_at: datetime | None


@router.get("/stats", response_model=CorpusStats)
async def stats(
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
) -> CorpusStats:
    """Roll-up counters over the chunks table. Cheap — one indexed scan."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
              COUNT(*)::int                                        AS total_chunks,
              COUNT(DISTINCT source)::int                          AS total_sources,
              COUNT(*) FILTER (WHERE summary IS NOT NULL)::int     AS chunks_with_summary,
              COUNT(*) FILTER (WHERE embedding IS NOT NULL)::int   AS chunks_with_embedding,
              MAX(ingested_at)                                     AS last_ingest_at
            FROM chunks
            """
        )
        recipe_row = await conn.fetchval(
            """
            SELECT COUNT(DISTINCT r)::int
              FROM (SELECT unnest(recipes) AS r FROM chunks) t
            """,
        )
    assert row is not None
    return CorpusStats(
        total_chunks=row["total_chunks"] or 0,
        total_sources=row["total_sources"] or 0,
        total_recipes=recipe_row or 0,
        chunks_with_summary=row["chunks_with_summary"] or 0,
        chunks_with_embedding=row["chunks_with_embedding"] or 0,
        last_ingest_at=row["last_ingest_at"],
    )


@router.get("/sources", response_model=list[CorpusSource])
async def sources(
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    order: Annotated[str, Query(pattern="^(chunks|recent)$")] = "chunks",
) -> list[CorpusSource]:
    """Per-source roll-up. `order=chunks` ranks by chunk count
    (largest sources first); `order=recent` by most recent ingest."""
    order_clause = (
        "MAX(ingested_at) DESC NULLS LAST"
        if order == "recent"
        else "COUNT(*) DESC, MAX(ingested_at) DESC NULLS LAST"
    )
    async with pool.acquire() as conn:
        rows: Any = await conn.fetch(
            f"""
            SELECT source,
                   COUNT(*)::int                                  AS chunk_count,
                   COALESCE(ARRAY_AGG(DISTINCT r), ARRAY[]::TEXT[]) AS recipes,
                   MAX(ingested_at)                               AS last_ingest_at
              FROM chunks, unnest(COALESCE(recipes, ARRAY[]::TEXT[])) AS r
             GROUP BY source
             ORDER BY {order_clause}
             LIMIT $1
            """,
            limit,
        )
    return [
        CorpusSource(
            source=r["source"],
            chunk_count=r["chunk_count"],
            recipes=list(r["recipes"] or []),
            last_ingest_at=r["last_ingest_at"],
        )
        for r in rows
    ]
