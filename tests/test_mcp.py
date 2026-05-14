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

from lighthouse.core.graph import GraphNode, GraphSearchHit
from lighthouse.mcp.server import build_server
from tests.conftest import FakeGraph


@pytest.fixture
def server_with_fake():
    fake = FakeGraph()
    server = build_server(fake)
    return server, fake


async def test_mcp_lists_search_and_fetch_tools(server_with_fake) -> None:
    server, _ = server_with_fake
    tools = await server.list_tools()
    names = {t.name for t in tools}
    assert names == {"search", "fetch"}


async def test_mcp_search_marshals_graph_hits(server_with_fake) -> None:
    server, fake = server_with_fake
    fake.search_hits = [
        GraphSearchHit(
            node_id="edge-1",
            summary="Lighthouse runs on FalkorDB.",
            source_node_uuid="lh-node",
            target_node_uuid="fdb-node",
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    ]

    result = await server.call_tool("search", {"query": "graph backend", "top_k": 5})

    # FastMCP returns (content_blocks, structured_dict) when a tool
    # produces typed output; assert on the structured payload which
    # is the canonical shape MCP clients consume.
    _, structured = result
    hits = structured["result"]
    assert len(hits) == 1
    assert hits[0]["node_id"] == "edge-1"
    assert "FalkorDB" in hits[0]["summary"]
    assert hits[0]["source_node_id"] == "lh-node"
    assert hits[0]["valid_from"] == "2026-01-01T00:00:00+00:00"


async def test_mcp_fetch_returns_node(server_with_fake) -> None:
    server, fake = server_with_fake
    fake.nodes["n-1"] = GraphNode(
        node_id="n-1",
        name="FalkorDB",
        summary="A Redis-module graph database used as Lighthouse's default backend.",
        labels=["Entity", "Database"],
        attributes={"license": "BSL"},
    )

    result = await server.call_tool("fetch", {"node_id": "n-1"})
    _, structured = result
    # FastMCP wraps single-object returns under "result" for parity
    # with list returns. Unwrap before asserting.
    node = structured["result"]
    assert node["node_id"] == "n-1"
    assert node["name"] == "FalkorDB"
    assert "Database" in node["labels"]
    assert node["attributes"]["license"] == "BSL"


async def test_mcp_fetch_returns_null_when_missing(server_with_fake) -> None:
    server, _ = server_with_fake
    result = await server.call_tool("fetch", {"node_id": "missing"})
    _, structured = result
    # FastMCP wraps a None return into {"result": None} when the
    # function's declared return type is Optional.
    assert structured.get("result") is None
