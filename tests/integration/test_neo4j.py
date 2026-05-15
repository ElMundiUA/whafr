"""End-to-end test against a real Neo4j 5.26 CE instance.

Gated by ``LIGHTHOUSE_INTEGRATION=1`` so day-to-day pytest runs don't
need Neo4j running. The compose file in ``infra/docker-compose.yml``
provisions Neo4j on bolt://localhost:7687 — start it before running
these (``docker compose -f infra/docker-compose.yml up -d neo4j``).

Smoke goal: prove the wrapper actually talks to the driver. We don't
test ingest here because that requires LLM keys (Graphiti calls out
for entity extraction); search-against-empty-graph is enough to fence
the wiring.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("LIGHTHOUSE_INTEGRATION") != "1",
    reason="set LIGHTHOUSE_INTEGRATION=1 to run integration tests",
)


@pytest.mark.asyncio
async def test_initialize_and_empty_search() -> None:
    """Initialise indices on a fresh graph and run a search.

    On a fresh graph ``search`` returns an empty list (no episodes
    ingested yet). What we're checking is that the Neo4j driver
    connects, that Graphiti can issue its queries, and that our
    wrapper projects the result into ``GraphSearchHit`` without
    crashing on the empty case.
    """
    from lighthouse.core.graph import KnowledgeGraph

    graph = KnowledgeGraph()
    try:
        await graph.initialize()
        hits = await graph.search("anything", top_k=5)
        assert isinstance(hits, list)
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_fetch_missing_returns_none() -> None:
    from lighthouse.core.graph import KnowledgeGraph

    graph = KnowledgeGraph()
    try:
        await graph.initialize()
        node = await graph.fetch("definitely-not-a-real-uuid")
        assert node is None
    finally:
        await graph.close()


@pytest.mark.asyncio
async def test_fetch_source_missing_returns_none() -> None:
    from lighthouse.core.graph import KnowledgeGraph

    graph = KnowledgeGraph()
    try:
        await graph.initialize()
        src = await graph.fetch_source("definitely-not-a-real-uuid")
        assert src is None
    finally:
        await graph.close()
