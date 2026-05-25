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

from fastapi import Header

from lighthouse.core.config import get_settings
from lighthouse.core.flat_graph import PUBLIC_WORKSPACE
from lighthouse.librarian.agent import Librarian
from lighthouse.proposals.queue import ProposalQueue
from lighthouse.proposals.store import GitProposalStore


async def get_workspace(
    x_workspace: Annotated[str | None, Header()] = None,
) -> str:
    """Resolve the tenant for a request from the ``X-Workspace`` header.

    A missing header maps to the reserved ``public`` workspace so the
    original single-tenant corpus (the harborgang public site) keeps
    working unchanged. Consumers that need isolation (Ship, per its
    workspace) send ``X-Workspace: <id>`` and only ever see their own
    rows — the FlatGraph layer filters every read on this value.
    """
    return x_workspace or PUBLIC_WORKSPACE


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
