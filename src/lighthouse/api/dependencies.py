"""FastAPI dependency factories.

Single place that wires shared resources (graph, proposal store,
librarian) into routes. Routes type-annotate their parameters with
``Annotated[X, Depends(get_X)]`` and never touch globals — that means
tests can swap any of these via ``app.dependency_overrides[...]``
without monkey-patching modules.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from fastapi import Header, HTTPException, Request

from lighthouse.core.auth import (
    RetrievalAuth,
    authenticate_retrieval,
    check_admin,
)
from lighthouse.core.config import get_settings
from lighthouse.core.flat_graph import PUBLIC_WORKSPACE
from lighthouse.core.query_log import QueryLogger
from lighthouse.core.ratelimit import SlidingWindowLimiter
from lighthouse.librarian.agent import Librarian
from lighthouse.proposals.queue import ProposalQueue
from lighthouse.proposals.store import GitProposalStore


async def get_workspace(
    x_workspace: Annotated[str | None, Header()] = None,
) -> str:
    """Resolve the tenant for an ADMIN request from the ``X-Workspace``
    header. Admin routes are gated by the operator's shared bearer
    (see :func:`require_admin`), so cross-workspace selection via the
    header is intended — the operator administers every tenant.

    Retrieval routes must NOT use this: they resolve the workspace via
    :func:`get_retrieval_auth`, which binds it to the API key.
    """
    return x_workspace or PUBLIC_WORKSPACE


def require_admin(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Shared admin guard — see :func:`lighthouse.core.auth.check_admin`."""
    check_admin(authorization)


async def get_retrieval_auth(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_workspace: Annotated[str | None, Header()] = None,
) -> RetrievalAuth:
    """Auth-aware workspace resolution for retrieval routes, plus the
    per-(workspace, ip) rate limit on the way in."""
    # The pool is fetched lazily (only when a key is actually presented),
    # so we can't declare it via Depends — resolve any test/deployment
    # override by hand to keep the dependency_overrides seam working.
    pool_factory = request.app.dependency_overrides.get(get_pg_pool, get_pg_pool)
    auth = await authenticate_retrieval(
        authorization=authorization,
        x_workspace=x_workspace,
        pool_factory=pool_factory,
    )
    limit = get_settings().lighthouse_search_rate_limit_per_minute
    if limit > 0:
        client_ip = request.client.host if request.client else "unknown"
        if not _rate_limiter().allow(f"{auth.workspace_id}:{client_ip}", limit):
            raise HTTPException(
                status_code=429, detail="rate limit exceeded — retry later"
            )
    return auth


@lru_cache(maxsize=1)
def _rate_limiter() -> SlidingWindowLimiter:
    return SlidingWindowLimiter()


@lru_cache(maxsize=1)
def get_graph() -> Any:
    """Process-singleton retrieval engine (pgvector flat-RAG).

    Cached so every request reuses the same connection pool. Tests
    override via ``app.dependency_overrides[get_graph]`` rather than
    touching the cache.
    """
    from lighthouse.core.flat_graph import FlatGraph

    return FlatGraph()


@lru_cache(maxsize=1)
def get_query_logger() -> QueryLogger:
    """Process-singleton fire-and-forget search analytics logger.

    Tests override via ``app.dependency_overrides[get_query_logger]``
    with a recording fake; the real one inserts into ``query_log`` and
    swallows every failure (analytics never break retrieval).
    """
    return QueryLogger(pool_factory=get_pg_pool)


@lru_cache(maxsize=1)
def get_proposal_store() -> GitProposalStore:
    """Process-singleton :class:`GitProposalStore` pointing at the
    configured proposals directory."""
    return GitProposalStore(Path(get_settings().lighthouse_proposals_dir))


@lru_cache(maxsize=1)
def get_librarian() -> Librarian:
    """Process-singleton :class:`Librarian` for proposal evaluation."""
    return Librarian()


_PG_POOL: Any | None = None


async def get_pg_pool() -> Any:
    """Lazy asyncpg pool against LIGHTHOUSE_PG_URL.

    Used by the admin importers router (and any future admin SQL).
    Mirrors the connection-string + pgbouncer treatment used in
    ``lighthouse.core.flat_graph``: strip Neon's pooler-only query
    params, disable statement caching.
    """
    global _PG_POOL
    if _PG_POOL is not None:
        return _PG_POOL
    import asyncpg

    from lighthouse.core.flat_graph import _strip_neon_extras

    url = get_settings().lighthouse_pg_url
    if not url:
        raise RuntimeError(
            "LIGHTHOUSE_PG_URL not set — admin importers need Postgres."
        )
    _PG_POOL = await asyncpg.create_pool(
        dsn=_strip_neon_extras(url),
        min_size=1,
        max_size=5,
        command_timeout=60,
        statement_cache_size=0,
    )
    return _PG_POOL


async def close_pg_pool() -> None:
    """Drain the asyncpg pool on shutdown so SIGTERM in a pod doesn't
    leave a half-open Postgres connection."""
    global _PG_POOL
    if _PG_POOL is not None:
        await _PG_POOL.close()
        _PG_POOL = None


@lru_cache(maxsize=1)
def get_proposal_queue() -> ProposalQueue:
    """Process-singleton :class:`ProposalQueue`.

    Wired against the same store / librarian / graph singletons the
    routes use. Bootstrapped from the FastAPI lifespan in
    :mod:`lighthouse.api.main` so the queue picks up any in-flight
    proposals from a prior crash before the API starts serving traffic.
    """
    return ProposalQueue(
        store=get_proposal_store(),
        librarian=get_librarian(),
        graph=get_graph(),
    )
