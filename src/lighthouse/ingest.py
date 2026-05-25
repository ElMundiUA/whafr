"""Shared ingest loop.

A single coroutine — :func:`drain` — that pulls :class:`SourceDocument`
instances from a connector and pushes each as an episode into a graph.
Used by both the CLI (``lighthouse ingest …``) and the source-runner's
scheduled flushes. Centralising it here means the error/log behaviour
is identical regardless of how ingestion got kicked off.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from lighthouse.connectors.base import Connector
from lighthouse.core.graph import KnowledgeGraph
from lighthouse.relevance import RelevanceGate

logger = logging.getLogger(__name__)


async def drain(
    connector: Connector,
    *,
    source_prefix: str,
    workspace_id: str,
    graph: KnowledgeGraph | Any | None = None,
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

    Delta-ingest: before paying for Graphiti's LLM extraction, we hash
    the new body and ask the graph layer whether the same source URL
    already has an episode with that exact content hash. If yes, skip
    the upsert entirely — recurrent ingest of unchanged sources costs
    ~one Neo4j read instead of N OpenAI calls.
    """
    owns_graph = graph is None
    g = graph or KnowledgeGraph()
    if owns_graph:
        await g.initialize()
    relevance = gate or RelevanceGate()

    n = 0
    skipped = 0
    failed = 0
    unchanged = 0
    try:
        async for doc in connector.ingest():
            if not await relevance.accept(title=doc.title, body=doc.body):
                skipped += 1
                logger.info("relevance gate rejected: %s", doc.title)
                continue
            # `source` is the canonical upstream identifier (URL or
            # github-tree ref); `recipe` is the slug claiming it
            # (RFC 9110 lives once, owned by multiple recipes). Delta
            # skip only when this *recipe* is already a member —
            # otherwise we still need to write so the row's recipes[]
            # picks up the new membership.
            source_canonical = doc.source_id
            body_hash = hashlib.sha256(doc.body.encode("utf-8")).hexdigest()
            try:
                if await g.has_unchanged_episode(
                    source_canonical,
                    body_hash,
                    recipe=source_prefix,
                    workspace_id=workspace_id,
                ):
                    unchanged += 1
                    logger.debug(
                        "delta-ingest skip (unchanged): %s", doc.source_id
                    )
                    continue
            except Exception:
                # Don't let the delta-check kill the run; fall back to
                # always-upsert when the lookup errors.
                logger.warning(
                    "delta-ingest check failed for %s — upserting anyway",
                    doc.source_id,
                )
            try:
                await g.upsert_episode(
                    name=doc.title,
                    body=doc.body,
                    source=source_canonical,
                    reference_time=doc.reference_time,
                    recipe=source_prefix,
                    workspace_id=workspace_id,
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
            "(gate skipped %d, unchanged %d, failed %d)",
            n,
            source_prefix,
            skipped,
            unchanged,
            failed,
        )
        return n
    finally:
        if owns_graph:
            await g.close()
