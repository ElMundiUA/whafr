"""FastAPI app entry point.

Two public surfaces:

- Retrieval (``/search``, ``/fetch_entity``, ``/fetch_source``,
  ``/health``) — public, no auth. Same shape an MCP server will wrap
  on top, plus the MCP transport itself mounted at ``/mcp/``.
- Proposal (``/v1/propose``, ``/v1/proposals/:id``) — guarded by a
  shared API key. Anyone with the key can submit a knowledge
  proposal; the librarian decides what makes it into the graph.

The app's :func:`lifespan` bootstraps the proposal queue on startup
(picks up any proposals stranded by a prior crash), enters the MCP
session-manager task group, and drains both on shutdown — so SIGTERM
in a container doesn't leave records stuck in ``evaluating`` or MCP
sessions dangling.

Tenant model: none. Isolation between Global and Project deployments
is by running separate processes against separate Postgres+Neo4j
stores.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from lighthouse import __version__
from lighthouse.api.admin_importers import router as admin_importers_router
from lighthouse.api.dependencies import close_pg_pool, get_proposal_queue
from lighthouse.api.proposal import router as proposal_router
from lighthouse.api.retrieval import router as retrieval_router
from lighthouse.mcp.server import build_server as build_mcp_server

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    # Build a fresh MCP server per app instance so each app owns its
    # own FastMCP session manager. The session manager isn't reentrant
    # across multiple ``run()`` contexts, which breaks every test that
    # spins up a new ``TestClient(app)`` instance unless we isolate.
    mcp_server = build_mcp_server()
    mcp_server.settings.streamable_http_path = "/"

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        queue = get_proposal_queue()
        try:
            n = await queue.bootstrap()
            if n:
                logger.info("bootstrapped %d pending proposals from store", n)
        except Exception:
            logger.exception("proposal queue bootstrap failed")
        async with mcp_server.session_manager.run():
            try:
                yield
            finally:
                try:
                    await queue.drain()
                except Exception:
                    logger.exception("proposal queue drain failed")
                try:
                    await close_pg_pool()
                except Exception:
                    logger.exception("admin asyncpg pool close failed")

    app = FastAPI(
        title="Lighthouse",
        version=__version__,
        description=(
            "Knowledge base for AI agents. Public retrieval over HTTP "
            "(MCP wrapper on top), proposal pipeline behind a shared API key."
        ),
        lifespan=lifespan,
    )

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(retrieval_router)
    app.include_router(proposal_router)
    app.include_router(admin_importers_router)

    # MCP (streamable-http) mounted at /mcp/ — its task-group lifetime
    # is owned by the lifespan above.
    app.mount("/mcp", mcp_server.streamable_http_app())

    return app


app = create_app()
