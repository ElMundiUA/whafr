"""MCP server adapter.

Exposes tools to MCP clients (Claude Desktop, Cursor, the Ship
navigator, anything else that speaks MCP):

- ``search`` — hybrid retrieval, returns ranked chunks
- ``fetch_source`` — pull one chunk's full text by uuid
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

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field

from lighthouse.core.flat_graph import PUBLIC_WORKSPACE, FlatGraph
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


def _workspace_from_ctx(ctx: Context) -> str:
    """Resolve the tenant for one MCP request from its ``X-Workspace``
    header. HTTP transports carry a raw request whose headers we read;
    stdio (local single-tenant desktop) has none, so we fall back to the
    reserved ``public`` workspace. The FlatGraph layer filters every read
    on the returned value, so this is the per-request isolation boundary.
    """
    try:
        request = ctx.request_context.request
    except Exception:
        return PUBLIC_WORKSPACE
    headers = getattr(request, "headers", None)
    if headers is not None:
        ws = headers.get("x-workspace")
        if ws:
            return ws
    return PUBLIC_WORKSPACE


class McpSearchHit(BaseModel):
    """One hit returned by the ``search`` MCP tool.

    Mirrors :class:`lighthouse.api.retrieval.SearchHit` so MCP clients
    and HTTP clients see the same shape. Each hit is an indexed chunk:
    ``summary`` is its heading + snippet and ``episode_ids`` carries the
    chunk uuid — feed it to ``fetch_source`` for the full text.
    ``source_node_id`` / ``target_node_id`` / ``valid_until`` are kept
    for wire compatibility but are always null (no entity layer).
    """

    node_id: str
    summary: str
    source_node_id: str | None = None
    target_node_id: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    episode_ids: list[str] = Field(default_factory=list)


class McpSearchResponse(BaseModel):
    """Wrapper returned by ``search`` so callers see retrieval coverage,
    not just raw hits. ``coverage`` is a coarse self-assessment:

    - ``ok`` — retrieval returned a meaningful number of hits and at
      least one looked confident. Treat as a normal grounded answer.
    - ``thin`` — fewer hits than asked for OR none look strongly
      relevant. Lean less on the corpus and flag uncertainty to the
      user.
    - ``empty`` — corpus has nothing useful for this query. Continue
      from model memory rather than fabricating citations.

    Agents that surface this to the user can say "low confidence — this
    topic isn't well covered in Lighthouse yet" instead of pretending
    the few hits are authoritative.
    """

    hits: list[McpSearchHit] = Field(default_factory=list)
    coverage: Literal["ok", "thin", "empty"] = "ok"
    coverage_note: str = ""


class McpEntity(BaseModel):
    """Response shape of the ``fetch_entity`` compatibility tool.

    The flat-RAG engine has no entity layer, so ``fetch_entity`` always
    returns null; this model is retained only so the tool's schema is
    stable for older clients.
    """

    node_id: str
    name: str
    summary: str
    labels: list[str] = Field(default_factory=list)
    attributes: dict[str, str] = Field(default_factory=dict)


class McpSource(BaseModel):
    """One chunk returned by ``fetch_source``.

    The raw ingested text — typically a paragraph or short section of
    the original article. Use this when a search summary isn't enough
    and you want the surrounding context in one shot.
    """

    episode_id: str
    name: str
    source: str
    content: str
    truncated: bool = False
    full_length: int = 0
    created_at: str | None = None
    valid_at: str | None = None


class McpProposalReceipt(BaseModel):
    proposal_id: str
    status: ProposalStatus = "queued"


def build_server(
    graph: FlatGraph | None = None,
    *,
    store: GitProposalStore | None = None,
    librarian: Librarian | None = None,
    queue: ProposalQueue | None = None,
) -> FastMCP:
    """Wire a :class:`FastMCP` instance with all four tools against the
    :class:`~lighthouse.core.flat_graph.FlatGraph` retrieval engine.

    Each dependency is a parameter so tests inject fakes without
    touching this module. Production constructs real instances in
    :func:`lighthouse.cli._mcp` once and threads them through.

    The ``queue`` is optional: if not passed but ``store`` + ``librarian``
    are, we build one on the fly so a CLI ``lighthouse mcp`` doesn't
    require the caller to wire the queue explicitly. Tests pass a fake
    queue to verify ``submit`` is called without spinning a real worker.
    """
    g = graph or FlatGraph()
    s = store
    lib = librarian
    q = queue
    if q is None and s is not None and lib is not None:
        q = ProposalQueue(store=s, librarian=lib, graph=g)

    mcp = FastMCP(
        name="lighthouse",
        instructions=(
            "Knowledge base for AI agents — indexed chunks of public "
            "SDLC reference material.\n\n"
            "Tools:\n"
            "• `search(query, top_k)` — hybrid (BM25 + vector + rerank) "
            "retrieval. Returns ranked chunks: a `summary` (heading + "
            "snippet) and an `episode_ids` uuid for each.\n"
            "• `fetch_source(episode_id)` — pull a chunk's FULL text "
            "(a few KB) by its `episode_ids` uuid. Use when a search "
            "summary isn't enough.\n"
            "• `propose(content, type, ...)` — submit a knowledge "
            "proposal for the librarian's review queue. Poll status "
            "via GET /v1/proposals/{id} on the HTTP API.\n\n"
            "Typical flow: one `search` → scan summaries → if a summary "
            "suffices, use it; otherwise `fetch_source` on that hit's "
            "episode_ids[0] for the full chunk."
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
            "Find relevant chunks in the knowledge base by "
            "natural-language query. Hybrid retrieval: BM25 + vector "
            "similarity + cross-encoder rerank. Each hit is one indexed "
            "chunk. Fields:\n"
            "• `node_id` — the chunk's uuid\n"
            "• `summary` — the chunk's heading + snippet\n"
            "• `episode_ids` — the chunk uuid; feed it to `fetch_source` "
            "to read the full text\n\n"
            "Use 3-7 word natural queries. Lower `top_k` (default 10) "
            "if you only need the strongest match."
        ),
    )
    async def search(
        query: str, ctx: Context, top_k: int = 10
    ) -> McpSearchResponse:
        hits = await g.search(
            query, top_k=top_k, workspace_id=_workspace_from_ctx(ctx)
        )
        wire_hits = [
            McpSearchHit(
                node_id=h.node_id,
                summary=h.summary,
                source_node_id=None,
                target_node_id=None,
                valid_from=(
                    h.published_at.isoformat() if h.published_at else None
                ),
                valid_until=None,
                episode_ids=list(h.episode_ids),
            )
            for h in hits
        ]
        # Coverage heuristic — we don't expose raw cross-encoder scores,
        # so use hit-count vs request as a proxy. Backed by audit data:
        # well-covered domains (Mobile, Security) routinely return ≥70 %
        # of top_k; thinly-covered domains return <40 %.
        if not wire_hits:
            coverage: Literal["ok", "thin", "empty"] = "empty"
            note = (
                "Corpus returned no hits — this topic isn't covered "
                "yet. Answer from memory and flag the gap to the user."
            )
        elif len(wire_hits) < max(2, int(top_k * 0.4)):
            coverage = "thin"
            note = (
                f"Only {len(wire_hits)}/{top_k} hits — sparse coverage. "
                "Use what's here but don't overweight it; flag "
                "uncertainty in your answer."
            )
        else:
            coverage = "ok"
            note = ""
        return McpSearchResponse(
            hits=wire_hits, coverage=coverage, coverage_note=note
        )

    @mcp.tool(
        name="fetch_entity",
        description=(
            "Compatibility no-op on this corpus: the flat-RAG engine has "
            "no entity layer, so this always returns null. Use "
            "`fetch_source` with a search hit's `episode_ids` uuid to "
            "read the underlying chunk instead."
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
            "Pull a chunk's full text. Pass the `episode_ids` uuid from "
            "a search hit.\n\n"
            "Returns name, source URL, and content. By default truncates "
            "to ~6 KB to keep your context tight; pass `max_chars` to "
            "request a shorter (e.g. 1500 for frontier models that "
            "already know the topic well) or longer cap. A `truncated` "
            "flag on the response tells you when the body was cut.\n\n"
            "Use this when a search summary is ambiguous or you need the "
            "surrounding context."
        ),
    )
    async def fetch_source(
        episode_id: str, ctx: Context, max_chars: int = 6000
    ) -> McpSource | None:
        src = await g.fetch_source(
            episode_id, workspace_id=_workspace_from_ctx(ctx)
        )
        if src is None:
            return None
        # Clamp to sane range — 200 chars is a single sentence (still
        # useful for confirming a detail); 20 KB is the upper bound to
        # keep one fetch from blowing a 200 K context.
        cap = max(200, min(int(max_chars), 20000))
        body = src.content or ""
        truncated = len(body) > cap
        if truncated:
            body = body[:cap]
        return McpSource(
            episode_id=src.episode_id,
            name=src.name,
            source=src.source,
            content=body,
            truncated=truncated,
            full_length=len(src.content or ""),
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
    graph: FlatGraph | None = None,
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
    graph: FlatGraph | None = None,
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
