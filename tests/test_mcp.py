"""MCP adapter unit tests.

We don't spawn a stdio process — instead we build the FastMCP instance
with a FakeGraph and invoke tools through ``call_tool`` the same way
the MCP runtime would. That gets us coverage of:

- Tool registration (names, descriptions, ordering)
- Tool routing (correct function fires for a given name)
- Argument and return-value marshalling (Pydantic models on each side)

The actual transport (stdio vs HTTP) is FastMCP's responsibility; we
trust its own test suite for that.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from lighthouse.core.flat_graph import FlatHit
from lighthouse.mcp.server import build_server
from tests.conftest import FakeGraph


@pytest.fixture
def server_with_fake(proposal_store, fake_librarian):
    fake = FakeGraph()
    server = build_server(fake, store=proposal_store, librarian=fake_librarian)
    return server, fake


async def test_mcp_lists_all_four_tools(server_with_fake) -> None:
    server, _ = server_with_fake
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert names == {"search", "fetch_entity", "fetch_source", "propose"}


async def test_mcp_search_marshals_flat_hits(server_with_fake) -> None:
    server, fake = server_with_fake
    fake.search_hits = [
        FlatHit(
            node_id="chunk-1",
            summary="Lighthouse runs on Postgres + pgvector.",
            source="https://example.com/doc",
            published_at=datetime(2026, 1, 1, tzinfo=UTC),
            episode_ids=["chunk-1"],
        ),
    ]

    result = await server.call_tool("search", {"query": "backend", "top_k": 5})

    # FastMCP returns (content_blocks, structured_dict). A BaseModel
    # return (McpSearchResponse) serialises its fields at the top level,
    # so hits live under "hits".
    _, structured = result
    hits = structured["hits"]
    assert len(hits) == 1
    assert hits[0]["node_id"] == "chunk-1"
    assert "pgvector" in hits[0]["summary"]
    assert hits[0]["valid_from"] == "2026-01-01T00:00:00+00:00"
    # No entity layer in flat-RAG.
    assert hits[0]["source_node_id"] is None
    assert hits[0]["episode_ids"] == ["chunk-1"]


async def test_mcp_fetch_entity_is_inert(server_with_fake) -> None:
    # Flat-RAG has no entity layer — fetch_entity always returns null.
    server, _ = server_with_fake
    result = await server.call_tool("fetch_entity", {"node_id": "anything"})
    _, structured = result
    assert structured.get("result") is None


async def test_mcp_propose_queues_proposal(
    server_with_fake, proposal_store, fake_librarian
) -> None:
    server, fake_graph = server_with_fake
    fake_librarian.next_decision = "accept"
    fake_librarian.next_reason = "matches docs"

    _, structured = await server.call_tool(
        "propose",
        {
            "content": "FastAPI 0.115 supports lifespan context managers.",
            "type": "add",
            "evidence": ["https://fastapi.tiangolo.com/release-notes/"],
            "rationale": "release notes",
            "submitted_by": "mcp-smoke",
        },
    )
    # Non-Optional return types come back un-wrapped from FastMCP, so
    # the receipt model serialises directly into ``structured``.
    proposal_id = structured["proposal_id"]
    assert structured["status"] == "queued"
    assert proposal_id  # uuid present

    # The record must have landed in the store before the receipt was
    # returned — this is the durability contract the worker relies on.
    record = await proposal_store.read(proposal_id)
    assert record is not None
    assert record.content.startswith("FastAPI 0.115")
    assert record.submitted_by == "mcp-smoke"
    assert record.evidence == ["https://fastapi.tiangolo.com/release-notes/"]


async def test_mcp_propose_without_store_raises() -> None:
    """If build_server is constructed search-only (no store/librarian),
    calling propose must error rather than silently dropping the
    submission. Regression fence so an operator misconfiguring stdio
    server gets a loud signal."""
    server = build_server(FakeGraph())  # no store, no librarian
    with pytest.raises(Exception):  # RuntimeError wrapped by FastMCP
        await server.call_tool("propose", {"content": "anything"})
