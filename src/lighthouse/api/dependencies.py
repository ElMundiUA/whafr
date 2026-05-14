"""FastAPI dependency factories.

Single place that wires shared resources (the graph client today, more
later) into routes. Routes type-annotate their parameters with
``Annotated[X, Depends(get_X)]`` and never touch globals — that means
tests can swap any of these via ``app.dependency_overrides[...]``
without monkey-patching modules.
"""

from __future__ import annotations

from functools import lru_cache

from lighthouse.core.graph import KnowledgeGraph


@lru_cache(maxsize=1)
def get_graph() -> KnowledgeGraph:
    """Process-singleton :class:`KnowledgeGraph`.

    Cached so every request reuses the same FalkorDB connection. The
    cache is cleared between test runs via ``get_graph.cache_clear()``
    when a fake graph is injected; production never clears it.
    """
    return KnowledgeGraph()
