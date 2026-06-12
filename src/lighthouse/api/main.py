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

Tenant model: row-level. One engine + one Postgres serves many
workspaces; every read/write carries a workspace_id (the reserved
``public`` workspace holds the single-tenant reference corpus).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from lighthouse.core.metrics import HTTP_LATENCY, HTTP_REQUESTS
from lighthouse.core.metrics import render as render_metrics

from lighthouse import __version__
from lighthouse.api.admin_importers import router as importers_router
from lighthouse.api.dependencies import (
    close_pg_pool,
    get_graph,
    get_pg_pool,
    get_proposal_queue,
)
from lighthouse.api.proposal import router as proposal_router
from lighthouse.api.retrieval import router as retrieval_router
from lighthouse.api.v1_analytics import router as v1_analytics_router
from lighthouse.api.v1_corpus import router as v1_corpus_router
from lighthouse.api.v1_keys import router as v1_keys_router
from lighthouse.api.v1_usage import router as v1_usage_router
from lighthouse.api.v1_webhooks import router as v1_webhooks_router
from lighthouse.api.v1_workspaces import router as v1_workspaces_router
from lighthouse.importers import store as importer_store
from lighthouse.importers.runner import run_queue_worker
from lighthouse.mcp.server import build_server as build_mcp_server
from lighthouse.webhooks.dispatcher import run_worker as run_webhook_worker

logger = logging.getLogger(__name__)


def _preflight_warnings() -> None:
    """Loud startup diagnostics for the misconfigurations that
    otherwise surface as confusing failures mid-flight."""
    import os

    from lighthouse.core.auth import admin_token_configured, insecure_admin_allowed
    from lighthouse.core.config import get_settings

    if not admin_token_configured():
        if insecure_admin_allowed():
            logger.warning(
                "admin surface is OPEN (LIGHTHOUSE_INSECURE_ADMIN=true) — "
                "never run this configuration on a reachable network"
            )
        else:
            logger.error(
                "LIGHTHOUSE_ADMIN_TOKEN is not set — every admin endpoint "
                "(/v1/importers, /v1/webhooks, /v1/corpus, /v1/analytics, "
                "/v1/keys, the /ui admin panel) will return 401. Set the "
                "token, or LIGHTHOUSE_INSECURE_ADMIN=true for local dev."
            )
    if not os.environ.get("LIGHTHOUSE_SECRETS_KEY"):
        logger.warning(
            "LIGHTHOUSE_SECRETS_KEY is not set — creating importers with "
            "credentials (Notion, Jira, S3, …) will fail. Generate one: "
            "python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )
    if not get_settings().openai_api_key:
        logger.warning(
            "OPENAI_API_KEY is not set — running in keyword-only (BM25) "
            "search mode; no embeddings will be computed or queried"
        )


def create_app() -> FastAPI:
    # Build a fresh MCP server per app instance so each app owns its
    # own FastMCP session manager. The session manager isn't reentrant
    # across multiple ``run()`` contexts, which breaks every test that
    # spins up a new ``TestClient(app)`` instance unless we isolate.
    mcp_server = build_mcp_server()
    mcp_server.settings.streamable_http_path = "/"

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        _preflight_warnings()
        queue = get_proposal_queue()
        try:
            n = await queue.bootstrap()
            if n:
                logger.info("bootstrapped %d pending proposals from store", n)
        except Exception:
            logger.exception("proposal queue bootstrap failed")
        # Recover importer runs stranded by the previous pod: the boot
        # sweep re-queues orphaned 'running' rows (once per run) and the
        # run-queue worker below picks them up. Cheap: at most a few
        # rows in flight at once.
        webhook_worker_task: asyncio.Task[None] | None = None
        run_worker_task: asyncio.Task[None] | None = None
        try:
            pool = await get_pg_pool()
            # Apply pending SQL migrations up-front. Previously only the
            # ingest paths ran them (via FlatGraph.initialize), so an
            # API-only process never saw new tables (query_log broke
            # /v1/analytics on otherwise-healthy deployments).
            from lighthouse.core.config import get_settings
            from lighthouse.core.migrator import run_migrations

            async with pool.acquire() as conn:
                applied = await run_migrations(
                    conn,
                    embedding_dim=int(get_settings().openai_embedding_dim),
                )
                if applied:
                    logger.info("applied migrations: %s", ", ".join(applied))
                swept = await importer_store.sweep_orphans(conn)
            if swept:
                logger.info("importer sweep: re-queued %d orphan run(s)", swept)
            # Webhook delivery worker — drains `webhook_deliveries` queue,
            # POSTs to subscribers with HMAC, retries with backoff.
            webhook_worker_task = asyncio.create_task(
                run_webhook_worker(pool), name="webhook-worker"
            )
            # Importer run-queue worker — claims queued runs (SKIP
            # LOCKED, replica-safe) and executes them.
            run_worker_task = asyncio.create_task(
                run_queue_worker(pool), name="run-queue-worker"
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
                for task in (webhook_worker_task, run_worker_task):
                    if task is not None:
                        task.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await task
                try:
                    await close_pg_pool()
                except Exception:
                    logger.exception("admin asyncpg pool close failed")
                # The retrieval engine holds its own asyncpg pool
                # (lazy, lru-cached singleton) — drain it too so a
                # SIGTERM'd pod leaves no half-open connections.
                try:
                    await get_graph().close()
                except Exception:
                    logger.exception("graph pool close failed")

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

    @app.get("/metrics", tags=["meta"], include_in_schema=False)
    async def metrics() -> Response:
        """Prometheus exposition. Unauthenticated by convention —
        scrapers live inside the network; firewall it at the ingress
        for public deployments (noted in SECURITY.md)."""
        body, content_type = render_metrics()
        return Response(content=body, media_type=content_type)

    @app.middleware("http")
    async def _http_metrics(request: Request, call_next: Any) -> Any:
        started = time.monotonic()
        response = await call_next(request)
        route = request.scope.get("route")
        # Route templates only — raw paths would explode cardinality.
        template = getattr(route, "path", None)
        if template:
            method = request.method
            HTTP_REQUESTS.labels(
                method=method, route=template, status=str(response.status_code)
            ).inc()
            HTTP_LATENCY.labels(method=method, route=template).observe(
                time.monotonic() - started
            )
        return response

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
    app.include_router(v1_analytics_router)
    app.include_router(v1_keys_router)
    app.include_router(v1_workspaces_router)
    app.include_router(v1_usage_router)

    # MCP (streamable-http) mounted at /mcp/ — its task-group lifetime
    # is owned by the lifespan above.
    app.mount("/mcp", mcp_server.streamable_http_app())

    # Built-in admin UI — static SPA over the /v1 surface. Ships in the
    # wheel; admin token + workspace are configured in the UI itself.
    from lighthouse.ui import STATIC_DIR

    app.mount("/ui", StaticFiles(directory=STATIC_DIR, html=True), name="ui")

    return app


app = create_app()
