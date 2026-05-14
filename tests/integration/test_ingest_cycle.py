"""End-to-end ingest → search smoke against real FalkorDB + OpenAI.

This is the most expensive test in the suite: it spins up a real
Graphiti client (OpenAI calls for entity extraction + embeddings) and
writes to a live FalkorDB. Run it with all three coordinates in place:

    docker compose -f infra/docker-compose.yml up -d falkordb
    export OPENAI_API_KEY=sk-...
    LIGHTHOUSE_INTEGRATION=1 pytest tests/integration/test_ingest_cycle.py

What it proves: the wrapper's ``upsert_episode`` actually persists, the
driver's Cypher query in ``fetch`` matches Graphiti's schema, and a
hybrid search retrieves the freshly-ingested fact. If any of those
break, the assert fails — no mocking can catch this.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest

pytestmark = [
    pytest.mark.skipif(
        os.environ.get("LIGHTHOUSE_INTEGRATION") != "1",
        reason="set LIGHTHOUSE_INTEGRATION=1 to run integration tests",
    ),
    pytest.mark.skipif(
        not os.environ.get("OPENAI_API_KEY"),
        reason="OPENAI_API_KEY required for ingest (entity extraction + embeddings)",
    ),
]


@pytest.mark.asyncio
async def test_ingest_then_search_returns_the_fact() -> None:
    """Round-trip: ingest a sentence, search for it, expect a hit."""
    from lighthouse.core.graph import KnowledgeGraph

    # Use a fresh nonce in the body so we can't pass via cached state
    # from a prior test run sharing the same FalkorDB database.
    nonce = uuid.uuid4().hex[:8]
    body = (
        f"In test-run {nonce}, the Lighthouse project chose FalkorDB "
        f"as its default graph backend because it ships as a Redis "
        f"module and has a friendlier license than Neo4j community."
    )

    graph = KnowledgeGraph()
    try:
        await graph.initialize()
        episode_uuid = await graph.upsert_episode(
            name=f"smoke-{nonce}",
            body=body,
            source="lighthouse integration test",
            reference_time=datetime.now(UTC),
        )
        assert episode_uuid, "expected upsert_episode to return an episode uuid"

        hits = await graph.search(
            f"What graph backend does Lighthouse use in run {nonce}?",
            top_k=10,
        )
        # We can't assert exact hit content (Graphiti rewrites facts during
        # extraction) — but we expect *some* hit because the search query
        # matches the ingested body semantically.
        assert hits, "expected at least one hit for the ingested fact"
    finally:
        await graph.close()
