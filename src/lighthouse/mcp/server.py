"""MCP server adapter.

Exposes the same two operations as the HTTP retrieval API — ``search``
and ``fetch`` — as MCP tools. Any MCP client (Claude Desktop, Cursor,
the Ship navigator) can plug this in and call the tools without writing
HTTP code.

Why a separate adapter rather than the HTTP API doubling as the MCP
surface? Two reasons:

1. **Different transport ergonomics.** MCP defaults to stdio for desktop
   clients (one process per session, parent talks JSON-RPC over pipes);
   HTTP needs a long-running server with auth. Keeping them as separate
   entry points lets each have its own deployment story.
2. **Schema control.** FastMCP introspects the tool function's
   signature to generate the schema MCP clients see. Reusing our own
   typed models means clients get structured output instead of a string
   blob — which Claude Desktop and Cursor render as proper tables.

Both adapters share :class:`KnowledgeGraph`; this module just adds the
MCP framing.
"""

from __future__ import annotations

import logging
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from lighthouse.core.graph import KnowledgeGraph

logger = logging.getLogger(__name__)


class McpSearchHit(BaseModel):
    """One hit returned by the ``search`` MCP tool.

    Mirrors :class:`lighthouse.api.retrieval.SearchHit` so MCP clients
    and HTTP clients see the same shape. Kept as a distinct class so
    the MCP wire format can evolve independently if a client needs
    different framing.
    """

    node_id: str
    summary: str
    source_node_id: str | None = None
    target_node_id: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None


class McpNode(BaseModel):
    node_id: str
    name: str
    summary: str
    labels: list[str] = Field(default_factory=list)
    attributes: dict[str, str] = Field(default_factory=dict)


def build_server(graph: KnowledgeGraph | None = None) -> FastMCP:
    """Wire a ``FastMCP`` instance with ``search`` and ``fetch`` tools.

    The graph is a parameter so tests can inject a fake without
    importing this module at all. In production the CLI creates one
    instance and hands it in.
    """
    g = graph or KnowledgeGraph()
    mcp = FastMCP(
        name="lighthouse",
        instructions=(
            "Knowledge base for AI agents. Use `search` for natural-"
            "language lookups across the indexed facts (returns ranked "
            "edges with temporal windows). Use `fetch` with a node uuid "
            "to retrieve a specific entity's full record."
        ),
    )

    @mcp.tool(
        name="search",
        description=(
            "Hybrid search (vector + BM25 + graph BFS) over the knowledge "
            "base. Returns up to `top_k` ranked facts, each with a uuid, "
            "a natural-language summary, and validity window."
        ),
    )
    async def search(query: str, top_k: int = 10) -> list[McpSearchHit]:
        hits = await g.search(query, top_k=top_k)
        return [
            McpSearchHit(
                node_id=h.node_id,
                summary=h.summary,
                source_node_id=h.source_node_uuid or None,
                target_node_id=h.target_node_uuid or None,
                valid_from=h.valid_from.isoformat() if h.valid_from else None,
                valid_until=h.valid_until.isoformat() if h.valid_until else None,
            )
            for h in hits
        ]

    @mcp.tool(
        name="fetch",
        description=(
            "Fetch one entity node by its uuid. Use the `node_id` from "
            "a previous `search` hit's `source_node_id` or "
            "`target_node_id` to drill into the underlying entities."
        ),
    )
    async def fetch(node_id: str) -> McpNode | None:
        node = await g.fetch(node_id)
        if node is None:
            return None
        return McpNode(
            node_id=node.node_id,
            name=node.name,
            summary=node.summary,
            labels=list(node.labels),
            attributes={k: str(v) for k, v in node.attributes.items()},
        )

    return mcp


def run_stdio(graph: KnowledgeGraph | None = None) -> None:
    """Launch the MCP server over stdio.

    This is the transport Claude Desktop / Cursor expect when wiring a
    local MCP server: parent process spawns us, we read JSON-RPC frames
    on stdin and write replies on stdout. Anything we log must go to
    stderr so it doesn't corrupt the framing.
    """
    server = build_server(graph)
    server.run(transport="stdio")


def run_http(
    graph: KnowledgeGraph | None = None,
    host: str = "127.0.0.1",
    port: int = 8765,
    transport: Literal["sse", "streamable-http"] = "streamable-http",
) -> None:
    """Launch the MCP server over HTTP — for remote agents (e.g. our
    Ship navigator) that can't spawn a subprocess.

    ``streamable-http`` is the newer MCP transport that runs over a
    standard fetch-with-stream loop; ``sse`` is the legacy
    server-sent-events variant kept for older clients.
    """
    server = build_server(graph)
    server.settings.host = host
    server.settings.port = port
    server.run(transport=transport)
