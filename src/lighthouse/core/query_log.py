"""Fire-and-forget search analytics logging.

Every retrieval search appends one row to ``query_log`` (see migrations
0006/0007). Logging is strictly best-effort: a missing Postgres pool, a
failed insert, or a slow connection never delays or fails the search
itself — the insert runs in a detached task and swallows everything.

Gap detection happens here, in the detached task:

- zero hits → always a gap;
- hits present and ``lighthouse_gap_classifier_enabled`` → the hits'
  summaries are rated 1-5 by Claude Haiku (:func:`score_hits`); an
  average below :data:`USEFUL_THRESHOLD` marks the search as a gap
  ("uncertain answer" — results came back but none grounds an answer).
  The average is stored in ``useful_score`` for the analytics UI.

Routes receive a :class:`QueryLogger` via the ``get_query_logger``
dependency in :mod:`lighthouse.api.dependencies`, so tests can swap in
a recording fake through ``app.dependency_overrides``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from lighthouse.core.config import get_settings
from lighthouse.core.usefulness import USEFUL_THRESHOLD, score_hits

logger = logging.getLogger(__name__)

# The classifier prompt only needs enough of each summary to judge
# usefulness; cap so a pathological chunk doesn't blow up the prompt.
_SUMMARY_CHAR_CAP = 500

# Strong references to in-flight insert tasks — same pattern as the
# importer runner: without these Python may GC a Task mid-insert.
_INFLIGHT: set[asyncio.Task[None]] = set()


class QueryLogger:
    """Appends search events to the ``query_log`` table.

    ``pool_factory`` (sync or async callable returning an asyncpg-pool-
    shaped object) keeps the Postgres seam injectable — embedders and
    tests pass their own; the default lazily resolves the API's shared
    pool.
    """

    def __init__(self, pool_factory: Any = None) -> None:
        self._pool_factory = pool_factory

    def log(
        self,
        *,
        workspace_id: str,
        query: str,
        top_k: int,
        hit_count: int,
        top_sources: list[str],
        summaries: list[str] | None = None,
        top_score: float | None = None,
        api_key_id: Any = None,
        latency_ms: int | None = None,
    ) -> None:
        """Schedule the insert (and optional gap classification) and
        return immediately."""
        task = asyncio.create_task(
            self._insert(
                workspace_id=workspace_id,
                query=query,
                top_k=top_k,
                hit_count=hit_count,
                top_sources=top_sources,
                summaries=summaries or [],
                top_score=top_score,
                api_key_id=api_key_id,
                latency_ms=latency_ms,
            ),
            name="query-log",
        )
        _INFLIGHT.add(task)
        task.add_done_callback(_INFLIGHT.discard)

    async def _insert(
        self,
        *,
        workspace_id: str,
        query: str,
        top_k: int,
        hit_count: int,
        top_sources: list[str],
        summaries: list[str],
        top_score: float | None,
        api_key_id: Any,
        latency_ms: int | None,
    ) -> None:
        try:
            gap = hit_count == 0
            useful_score = await self._classify(query, summaries)
            if useful_score is not None and useful_score < USEFUL_THRESHOLD:
                gap = True
            pool = await self._pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO query_log
                        (workspace_id, query, top_k, hit_count,
                         top_sources, latency_ms, gap,
                         top_score, useful_score, api_key_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    workspace_id,
                    query,
                    top_k,
                    hit_count,
                    top_sources,
                    latency_ms,
                    gap,
                    top_score,
                    useful_score,
                    api_key_id,
                )
        except Exception:
            # Analytics must never break retrieval — log and move on.
            logger.debug("query_log insert failed", exc_info=True)

    async def _classify(
        self, query: str, summaries: list[str]
    ) -> float | None:
        """Average 1-5 usefulness of the hits, or None when the
        classifier is disabled / there is nothing to rate."""
        if not summaries or not get_settings().lighthouse_gap_classifier_enabled:
            return None
        scores = await score_hits(
            query, [s[:_SUMMARY_CHAR_CAP] for s in summaries]
        )
        return sum(scores) / len(scores) if scores else None

    async def _pool(self) -> Any:
        import inspect

        if self._pool_factory is not None:
            maybe = self._pool_factory()
            return await maybe if inspect.isawaitable(maybe) else maybe
        from lighthouse.api.dependencies import get_pg_pool

        return await get_pg_pool()
