"""Shared pytest fixtures.

The graph fake here is the canonical seam for unit tests: routes get a
:class:`FakeGraph` via ``app.dependency_overrides`` so we never touch
FalkorDB. Integration tests in ``tests/integration/`` opt out of this
and exercise the real backend; they're skipped by default and require
``LIGHTHOUSE_INTEGRATION=1`` to run.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lighthouse.api.dependencies import (
    get_graph,
    get_librarian,
    get_proposal_queue,
    get_proposal_store,
)
from lighthouse.api.main import app
from lighthouse.core.graph import GraphNode, GraphSearchHit, KnowledgeGraph
from lighthouse.librarian.agent import Librarian
from lighthouse.proposals.queue import ProposalQueue
from lighthouse.proposals.store import GitProposalStore


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


class FakeLibrarian(Librarian):
    """In-memory stand-in for the Anthropic-backed Librarian.

    Tests set ``next_decision`` / ``next_reason`` to script what the
    next call to :meth:`evaluate_proposal` returns. The fake also
    records every call in ``calls`` so tests can assert on the prompt
    payload (evidence, rationale, content).
    """

    def __init__(self) -> None:
        # Skip parent __init__ — we don't want Settings or Anthropic SDK.
        self.next_decision: str = "accept"
        self.next_reason: str = "looks good"
        self.calls: list[dict[str, object]] = []
        self.raise_on_next: Exception | None = None

    async def evaluate_proposal(  # type: ignore[override]
        self,
        *,
        proposal_type: str,
        content: str,
        evidence: list[str],
        rationale: str,
        target_node_id: str | None = None,
    ) -> tuple[str, str]:
        self.calls.append(
            {
                "proposal_type": proposal_type,
                "content": content,
                "evidence": list(evidence),
                "rationale": rationale,
                "target_node_id": target_node_id,
            }
        )
        if self.raise_on_next is not None:
            exc = self.raise_on_next
            self.raise_on_next = None
            raise exc
        return self.next_decision, self.next_reason  # type: ignore[return-value]


@pytest.fixture
def fake_graph() -> FakeGraph:
    return FakeGraph()


@pytest.fixture
def fake_librarian() -> FakeLibrarian:
    return FakeLibrarian()


@pytest.fixture
def proposal_store(tmp_path: Path) -> GitProposalStore:
    """Real GitProposalStore against an isolated tmp directory.

    We use the real store (not a mock) because its git interaction is
    the whole point — mocking it out would hide the bug a test of
    "proposal pipeline" most needs to catch.
    """
    return GitProposalStore(tmp_path / "proposals")


@pytest.fixture
def proposal_queue(
    proposal_store: GitProposalStore,
    fake_librarian: FakeLibrarian,
    fake_graph: FakeGraph,
) -> ProposalQueue:
    """A real ProposalQueue wired against the test fakes.

    We use the real queue (not a mock) because its bootstrap + drain
    semantics are precisely what we want exercised in higher-level
    tests. Workers execute against ``fake_librarian`` and ``fake_graph``
    so no API calls happen."""
    return ProposalQueue(
        store=proposal_store,
        librarian=fake_librarian,
        graph=fake_graph,
    )


@pytest.fixture
def client(
    fake_graph: FakeGraph,
    fake_librarian: FakeLibrarian,
    proposal_store: GitProposalStore,
    proposal_queue: ProposalQueue,
) -> TestClient:
    """TestClient with graph + librarian + store + queue dependencies
    overridden.

    Yielding semantics ensure overrides are removed after the test so
    cross-test bleed is impossible.
    """
    app.dependency_overrides[get_graph] = lambda: fake_graph
    app.dependency_overrides[get_librarian] = lambda: fake_librarian
    app.dependency_overrides[get_proposal_store] = lambda: proposal_store
    app.dependency_overrides[get_proposal_queue] = lambda: proposal_queue
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_graph, None)
        app.dependency_overrides.pop(get_librarian, None)
        app.dependency_overrides.pop(get_proposal_store, None)
        app.dependency_overrides.pop(get_proposal_queue, None)
