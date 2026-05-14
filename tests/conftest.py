"""Shared pytest fixtures.

The graph fake here is the canonical seam for unit tests: routes get a
:class:`FakeGraph` via ``app.dependency_overrides`` so we never touch
FalkorDB. Integration tests in ``tests/integration/`` opt out of this
and exercise the real backend; they're skipped by default and require
``LIGHTHOUSE_INTEGRATION=1`` to run.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from lighthouse.api.dependencies import get_graph
from lighthouse.api.main import app
from lighthouse.core.graph import GraphNode, GraphSearchHit, KnowledgeGraph


class FakeGraph(KnowledgeGraph):
    """In-memory stand-in for KnowledgeGraph.

    Tests configure ``search_hits`` and ``nodes`` then assert on what the
    routes do with them. ``ingested`` is a write log so ingestion-path
    tests can verify what the CLI actually passes through.
    """

    def __init__(self) -> None:
        # Skip parent __init__ — we don't want a real Settings load.
        self.search_hits: list[GraphSearchHit] = []
        self.nodes: dict[str, GraphNode] = {}
        self.ingested: list[dict[str, str]] = []
        self.initialized = False

    async def initialize(self) -> None:  # type: ignore[override]
        self.initialized = True

    async def search(  # type: ignore[override]
        self, query: str, top_k: int = 10
    ) -> list[GraphSearchHit]:
        return list(self.search_hits[:top_k])

    async def fetch(self, node_id: str) -> GraphNode | None:  # type: ignore[override]
        return self.nodes.get(node_id)

    async def upsert_episode(  # type: ignore[override]
        self,
        *,
        name: str,
        body: str,
        source: str,
        reference_time: datetime | None = None,
    ) -> str:
        self.ingested.append(
            {
                "name": name,
                "body": body,
                "source": source,
                "reference_time": (reference_time or datetime.now(UTC)).isoformat(),
            }
        )
        return f"fake-episode-{len(self.ingested)}"

    async def close(self) -> None:  # type: ignore[override]
        pass


@pytest.fixture
def fake_graph() -> FakeGraph:
    return FakeGraph()


@pytest.fixture
def client(fake_graph: FakeGraph) -> TestClient:
    """TestClient with the graph dependency overridden.

    Yielding semantics ensure the override is removed after the test
    so cross-test bleed is impossible.
    """
    app.dependency_overrides[get_graph] = lambda: fake_graph
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_graph, None)
