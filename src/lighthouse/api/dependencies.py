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

from lighthouse.core.config import get_settings
from lighthouse.core.graph import KnowledgeGraph
from lighthouse.librarian.agent import Librarian
from lighthouse.proposals.queue import ProposalQueue
from lighthouse.proposals.store import GitProposalStore


@lru_cache(maxsize=1)
def get_graph() -> KnowledgeGraph:
    """Process-singleton :class:`KnowledgeGraph`.

    Cached so every request reuses the same FalkorDB connection. The
    cache is cleared between test runs via ``get_graph.cache_clear()``
    when a fake graph is injected; production never clears it.
    """
    return KnowledgeGraph()


@lru_cache(maxsize=1)
def get_proposal_store() -> GitProposalStore:
    """Process-singleton :class:`GitProposalStore` pointing at the
    configured proposals directory."""
    return GitProposalStore(Path(get_settings().lighthouse_proposals_dir))


@lru_cache(maxsize=1)
def get_librarian() -> Librarian:
    """Process-singleton :class:`Librarian` for proposal evaluation."""
    return Librarian()


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
