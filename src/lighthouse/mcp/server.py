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
from mcp.server.transport_security import TransportSecuritySettings
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

    Each hit is a *fact* (a graph edge) — a one-line natural-language
    statement extracted from a source document. ``episode_ids`` are
    the uuids of the source chunks the fact was extracted from; feed
    one into ``fetch_source`` to read the original paragraphs.
    """

    node_id: str
    summary: str
    source_node_id: str | None = None
    target_node_id: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    episode_ids: list[str] = Field(default_factory=list)


class McpEntity(BaseModel):
    """One entity returned by ``fetch_fact``.

    A graph entity is the *thing* a fact talks about — a person, a
    framework, a concept. Search returns facts whose summaries
    reference entities by uuid; this tool resolves one of those uuids
    to the entity's full record (name, summary, labels, attributes).
    """

    node_id: str
    name: str
    summary: str
    labels: list[str] = Field(default_factory=list)
    attributes: dict[str, str] = Field(default_factory=dict)


class McpSource(BaseModel):
    """One source chunk returned by ``fetch_source``.

    A source is the raw ingested text the graph extracted facts and
    entities from — typically a paragraph or short section of the
    original article. Use this when a fact's one-line summary isn't
    enough and you want the surrounding context in one shot rather
    than chasing entities through multiple ``fetch_fact`` calls.
    """

    episode_id: str
    name: str
    source: str
    content: str
    created_at: str | None = None
    valid_at: str | None = None


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
            "Knowledge base for AI agents — a graph of facts extracted "
            "from public SDLC reference material.\n\n"
            "Tools:\n"
            "• `search(query, top_k)` — find facts. Returns ranked "
            "one-line statements + each fact's source_node_id / "
            "target_node_id (the entities it relates) and episode_ids "
            "(the source chunks it came from).\n"
            "• `fetch_entity(node_id)` — drill into ONE entity (person, "
            "concept, framework) referenced by a fact. Cheap but only "
            "returns name + summary + labels + attributes; usually you "
            "don't need this unless the fact's summary is ambiguous.\n"
            "• `fetch_source(episode_id)` — pull the ORIGINAL ingested "
            "paragraph the fact was extracted from (a few KB). Prefer "
            "this over multiple `fetch_entity` calls when the fact's "
            "one-line summary isn't enough — one round-trip vs N.\n"
            "• `propose(content, type, ...)` — submit a knowledge "
            "proposal for the librarian's review queue. Poll status "
            "via GET /v1/proposals/{id} on the HTTP API.\n\n"
            "Typical flow: one `search` → scan summaries → if the fact "
            "summary suffices, use it. If you need more, `fetch_source` "
            "on the best hit's episode_ids[0] — one call gets you the "
            "full context. Use `fetch_entity` only when you specifically "
            "need an entity's other attributes."
        ),
        # We sit behind a TLS-terminating ingress that already validates
        # the Host header. FastMCP's auto-enabled DNS rebinding protection
        # rejects any non-localhost host, which would block every cluster
        # request. Disable it explicitly here.
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        ),
    )

    @mcp.tool(
        name="search",
        description=(
            "Find facts in the knowledge base by natural-language query. "
            "Hybrid retrieval: BM25 + vector similarity + cross-encoder "
            "rerank. Each hit is one *fact* (a graph edge) — a single "
            "natural-language statement extracted from a source. Fields:\n"
            "• `node_id` — the fact's uuid\n"
            "• `summary` — the one-line statement (e.g. 'X wrote Y')\n"
            "• `source_node_id` / `target_node_id` — the entities the "
            "fact relates; feed either into `fetch_entity`\n"
            "• `episode_ids` — uuids of source chunks this fact came "
            "from; feed one into `fetch_source` to read the original "
            "paragraph.\n\n"
            "Use 3-7 word natural queries. Lower `top_k` (default 10) "
            "if you only need the strongest match."
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
                episode_ids=list(h.episode_ids),
            )
            for h in hits
        ]

    @mcp.tool(
        name="fetch_entity",
        description=(
            "Resolve one entity (person, concept, framework, tool) by "
            "uuid. Use the `source_node_id` or `target_node_id` from a "
            "prior `search` hit when the fact's one-line summary "
            "references an entity you want more detail on (e.g. fact "
            "'X wrote Y' → fetch_entity(uuid of Y) to see Y's other "
            "attributes and labels).\n\n"
            "Returns lightweight metadata (name, summary, labels, "
            "attributes), NOT the source paragraph. For source text use "
            "`fetch_source` — it's usually one call instead of several."
        ),
    )
    async def fetch_entity(node_id: str) -> McpEntity | None:
        node = await g.fetch(node_id)
        if node is None:
            return None
        return McpEntity(
            node_id=node.node_id,
            name=node.name,
            summary=node.summary,
            labels=list(node.labels),
            attributes={k: str(v) for k, v in node.attributes.items()},
        )

    @mcp.tool(
        name="fetch_source",
        description=(
            "Pull the original ingested paragraph a fact was extracted "
            "from. Pass any uuid from a search hit's `episode_ids`. "
            "Returns name, source URL, and full content (typically a "
            "few KB).\n\n"
            "Prefer this over multiple `fetch_entity` calls when a "
            "fact's one-line summary is ambiguous or you need the "
            "surrounding context. One round-trip vs N. Cost is the "
            "extra tokens in your context (2-5 KB per source vs "
            "0.5 KB per entity)."
        ),
    )
    async def fetch_source(episode_id: str) -> McpSource | None:
        src = await g.fetch_source(episode_id)
        if src is None:
            return None
        return McpSource(
            episode_id=src.episode_id,
            name=src.name,
            source=src.source,
            content=src.content,
            created_at=src.created_at.isoformat() if src.created_at else None,
            valid_at=src.valid_at.isoformat() if src.valid_at else None,
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
            "from a prior `search` / `fetch_entity` when correcting "
            "or deprecating an existing entity."
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
