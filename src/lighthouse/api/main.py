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

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from lighthouse import __version__
from lighthouse.api.admin_importers import router as importers_router
from lighthouse.api.dependencies import (
    close_pg_pool,
    get_pg_pool,
    get_proposal_queue,
)
from lighthouse.api.proposal import router as proposal_router
from lighthouse.api.retrieval import router as retrieval_router
from lighthouse.api.v1_corpus import router as v1_corpus_router
from lighthouse.api.v1_webhooks import router as v1_webhooks_router
from lighthouse.importers import store as importer_store
from lighthouse.mcp.server import build_server as build_mcp_server
from lighthouse.webhooks.dispatcher import run_worker as run_webhook_worker

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
        # Reset any importer rows / runs stranded by the previous pod —
        # asyncio.create_task'd runs don't survive a SIGTERM, so on each
        # boot we sweep stuck rows back to a usable state. Cheap: at
        # most a few rows in flight at once.
        webhook_worker_task: asyncio.Task[None] | None = None
        try:
            pool = await get_pg_pool()
            async with pool.acquire() as conn:
                swept = await importer_store.sweep_orphans(conn)
            if swept:
                logger.info("importer sweep: marked %d orphan run(s) as cancelled", swept)
            # Webhook delivery worker — drains `webhook_deliveries` queue,
            # POSTs to subscribers with HMAC, retries with backoff.
            webhook_worker_task = asyncio.create_task(
                run_webhook_worker(pool), name="webhook-worker"
            )
        except Exception:
            logger.exception("importer orphan-sweep / webhook-worker init failed")
        async with mcp_server.session_manager.run():
            try:
                yield
            finally:
                try:
                    await queue.drain()
                except Exception:
                    logger.exception("proposal queue drain failed")
                if webhook_worker_task is not None:
                    webhook_worker_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await webhook_worker_task
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
    # Legacy admin mount used by the in-cluster Astro UI.
    app.include_router(
        importers_router, prefix="/admin/importers", tags=["admin"]
    )
    # External /v1/importers — same handlers, semver-stable surface
    # for SDK + Ship integration.
    app.include_router(
        importers_router, prefix="/v1/importers", tags=["v1"]
    )
    app.include_router(v1_corpus_router)
    app.include_router(v1_webhooks_router)

    # MCP (streamable-http) mounted at /mcp/ — its task-group lifetime
    # is owned by the lifespan above.
    app.mount("/mcp", mcp_server.streamable_http_app())

    return app


app = create_app()
