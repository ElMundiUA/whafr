"""Shared ingest loop.

A single coroutine — :func:`drain` — that pulls :class:`SourceDocument`
instances from a connector and pushes each as an episode into a graph.
Used by both the CLI (``lighthouse ingest …``) and the source-runner's
scheduled flushes. Centralising it here means the error/log behaviour
is identical regardless of how ingestion got kicked off.
"""

from __future__ import annotations

import logging

from lighthouse.connectors.base import Connector
from lighthouse.core.graph import KnowledgeGraph
from lighthouse.relevance import RelevanceGate

logger = logging.getLogger(__name__)


async def drain(
    connector: Connector,
    *,
    source_prefix: str,
    graph: KnowledgeGraph | None = None,
    gate: RelevanceGate | None = None,
) -> int:
    """Run ``connector.ingest()`` and upsert each document as an episode.

    ``graph`` is a parameter so a long-running process (like the
    scheduler) can share one graph instance across many drains instead
    of opening/closing on each. When ``graph`` is ``None`` we open and
    close one ourselves — matches the one-shot CLI flow.

    ``gate`` is an optional :class:`RelevanceGate`. When enabled it
    runs a cheap LLM classifier on every doc before paying for full
    entity extraction — useful for whole-site crawls where 20-40% of
    pages are nav/marketing/error noise. Disabled gate is a no-op,
    so curated URL lists pay nothing.
    """
    owns_graph = graph is None
    g = graph or KnowledgeGraph()
    if owns_graph:
        await g.initialize()
    relevance = gate or RelevanceGate()

    n = 0
    skipped = 0
    failed = 0
    try:
        async for doc in connector.ingest():
            if not await relevance.accept(title=doc.title, body=doc.body):
                skipped += 1
                logger.info("relevance gate rejected: %s", doc.title)
                continue
            try:
                await g.upsert_episode(
                    name=doc.title,
                    body=doc.body,
                    source=f"{source_prefix}:{doc.source_id}",
                    reference_time=doc.reference_time,
                )
            except Exception:
                # Graphiti can raise Pydantic ValidationError on the
                # entity nodes it extracts (e.g. a numeric "name" that
                # fails ``string_type``), an LLM rate limit, or a
                # transient Neo4j error. Pre-fix, that killed the whole
                # source — a single bad page would silently truncate
                # ingest at the second URL of a 200-URL sitemap. Keep
                # the loop alive and log the failure instead.
                failed += 1
                logger.exception(
                    "upsert_episode failed for %s (skipping doc)",
                    doc.source_id,
                )
                continue
            n += 1
            logger.info("ingested: %s", doc.title)
        logger.info(
            "done — %d documents ingested from %s "
            "(gate skipped %d, failed %d)",
            n,
            source_prefix,
            skipped,
            failed,
        )
        return n
    finally:
        if owns_graph:
            await g.close()
