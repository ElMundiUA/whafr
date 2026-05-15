"""FastAPI app entry point.

Two public surfaces:

- Retrieval (``/search``, ``/fetch``, ``/health``) — public, no auth.
  Same shape an MCP server will wrap on top.
- Proposal (``/v1/propose``, ``/v1/proposals/:id``) — guarded by a
  shared API key. Anyone with the key can submit a knowledge
  proposal; the librarian decides what makes it into the graph.

The app's :func:`lifespan` bootstraps the proposal queue on startup
(picks up any proposals stranded by a prior crash) and drains
in-flight workers on shutdown — so SIGTERM in a container doesn't
leave records stuck in ``evaluating``.

Tenant model: none. Isolation between Global and Project deployments
is by running separate processes against separate Postgres+FalkorDB
stores.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from lighthouse import __version__
from lighthouse.api.dependencies import get_proposal_queue
from lighthouse.api.proposal import router as proposal_router
from lighthouse.api.retrieval import router as retrieval_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup: re-enqueue stranded proposals. Shutdown: drain workers."""
    queue = get_proposal_queue()
    try:
        n = await queue.bootstrap()
        if n:
            logger.info("bootstrapped %d pending proposals from store", n)
    except Exception:
        # We don't want a bootstrap failure to take the whole API
        # offline — log and proceed. The store still works, just no
        # automatic recovery this restart.
        logger.exception("proposal queue bootstrap failed")
    try:
        yield
    finally:
        try:
            await queue.drain()
        except Exception:
            logger.exception("proposal queue drain failed")


def create_app() -> FastAPI:
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

    return app


app = create_app()
