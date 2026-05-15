"""MCP server adapter.

Exposes three tools to MCP clients (Claude Desktop, Cursor, the Ship
navigator, anything else that speaks MCP):

- ``search`` — hybrid retrieval, returns ranked facts
- ``fetch`` — pull one entity node by uuid
- ``propose`` — submit a knowledge proposal for librarian review

Why a separate adapter rather than the HTTP API doubling as MCP:

1. **Different transport ergonomics.** MCP defaults to stdio for desktop
   clients (one process per session, parent talks JSON-RPC over pipes);
   HTTP needs a long-running server with its own auth. Keeping them
   separate lets each have its own deployment story.
2. **Schema control.** FastMCP introspects each tool function to
   generate the schema MCP clients see. Reusing our typed models means
   clients get structured output instead of string blobs — Claude
   Desktop and Cursor render that as proper tables.

Auth note: the MCP transport itself is the trust boundary. Stdio MCP
runs in-process with the user; HTTP MCP should sit behind a trusted
ingress. We don't bolt on a per-tool API key — if you're exposing the
HTTP MCP server publicly, gate the whole endpoint, not individual tools.
"""

from __future__ import annotations

import logging
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from lighthouse.core.graph import KnowledgeGraph
from lighthouse.librarian.agent import Librarian
from lighthouse.proposals.queue import ProposalQueue
from lighthouse.proposals.store import (
    GitProposalStore,
    ProposalRecord,
    ProposalStatus,
    new_proposal_id,
    utc_now,
)

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


class McpProposalReceipt(BaseModel):
    proposal_id: str
    status: ProposalStatus = "queued"


def build_server(
    graph: KnowledgeGraph | None = None,
    *,
    store: GitProposalStore | None = None,
    librarian: Librarian | None = None,
    queue: ProposalQueue | None = None,
) -> FastMCP:
    """Wire a :class:`FastMCP` instance with all three tools.

    Each dependency is a parameter so tests inject fakes without
    touching this module. Production constructs real instances in
    :func:`lighthouse.cli._mcp` once and threads them through.

    The ``queue`` is optional: if not passed but ``store`` + ``librarian``
    are, we build one on the fly so a CLI ``lighthouse mcp`` doesn't
    require the caller to wire the queue explicitly. Tests pass a fake
    queue to verify ``submit`` is called without spinning a real worker.
    """
    g = graph or KnowledgeGraph()
    s = store
    lib = librarian
    q = queue
    if q is None and s is not None and lib is not None:
        q = ProposalQueue(store=s, librarian=lib, graph=g)

    mcp = FastMCP(
        name="lighthouse",
        instructions=(
            "Knowledge base for AI agents. Use `search` for natural-"
            "language lookups across indexed facts (returns ranked "
            "edges with temporal windows). Use `fetch` with a node uuid "
            "to retrieve a specific entity's full record. Use `propose` "
            "to submit a new fact, correction, or deprecation — it goes "
            "through the librarian's review pipeline; you can poll its "
            "status via the HTTP API."
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

    @mcp.tool(
        name="propose",
        description=(
            "Submit a knowledge proposal for librarian review. The "
            "proposal is queued immediately; the librarian decides "
            "asynchronously whether to accept (write to graph), reject "
            "(return reason), or escalate (human review). Poll status "
            "via GET /v1/proposals/{id} on the HTTP API. `type` is one "
            "of 'add', 'correct', 'deprecate'. Pass `target_node_id` "
            "from a prior `search`/`fetch` when correcting or deprecating."
        ),
    )
    async def propose(
        content: str,
        type: Literal["add", "correct", "deprecate"] = "add",
        target_node_id: str | None = None,
        evidence: list[str] | None = None,
        rationale: str = "",
        submitted_by: str = "mcp-client",
    ) -> McpProposalReceipt:
        if s is None or q is None:
            # build_server() was called search-only mode (no store/librarian
            # passed). Refuse loudly rather than silently dropping the
            # proposal — surfaces a config bug to the operator.
            raise RuntimeError(
                "MCP propose tool requires store + librarian — server "
                "was built without them"
            )

        proposal_id = new_proposal_id()
        record = ProposalRecord(
            id=proposal_id,
            status="queued",
            type=type,
            content=content,
            submitted_at=utc_now(),
            submitted_by=submitted_by,
            target_node_id=target_node_id,
            evidence=list(evidence or []),
            rationale=rationale,
        )
        await s.create(record)
        await q.submit(proposal_id)
        return McpProposalReceipt(proposal_id=proposal_id, status="queued")

    return mcp


def run_stdio(
    graph: KnowledgeGraph | None = None,
    *,
    store: GitProposalStore | None = None,
    librarian: Librarian | None = None,
    queue: ProposalQueue | None = None,
) -> None:
    """Launch the MCP server over stdio.

    This is the transport Claude Desktop / Cursor expect when wiring a
    local MCP server: parent process spawns us, we read JSON-RPC frames
    on stdin and write replies on stdout. Anything we log must go to
    stderr so it doesn't corrupt the framing.
    """
    server = build_server(graph, store=store, librarian=librarian, queue=queue)
    server.run(transport="stdio")


def run_http(
    graph: KnowledgeGraph | None = None,
    *,
    store: GitProposalStore | None = None,
    librarian: Librarian | None = None,
    queue: ProposalQueue | None = None,
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
    server = build_server(graph, store=store, librarian=librarian, queue=queue)
    server.settings.host = host
    server.settings.port = port
    server.run(transport=transport)
