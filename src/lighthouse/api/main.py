"""FastAPI app entry point.

Two public surfaces:

- Retrieval (``/search``, ``/fetch``, ``/health``) — public, no auth.
  Same shape an MCP server will wrap on top.
- Proposal (``/propose``) — guarded by a shared API key. Anyone with the key
  can submit a knowledge proposal; the librarian decides what makes it into
  the graph.

Tenant model: none. Isolation between Global and Project deployments is by
running separate processes against separate Postgres+FalkorDB stores.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from lighthouse import __version__
from lighthouse.api.proposal import router as proposal_router
from lighthouse.api.retrieval import router as retrieval_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Lighthouse",
        version=__version__,
        description=(
            "Knowledge base for AI agents. Public retrieval over HTTP "
            "(MCP wrapper on top), proposal pipeline behind a shared API key."
        ),
    )

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(retrieval_router)
    app.include_router(proposal_router)

    return app


app = create_app()


