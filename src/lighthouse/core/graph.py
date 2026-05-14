"""Knowledge graph wrapper around Graphiti + FalkorDB.

Graphiti gives us temporal knowledge graph semantics out of the box:
each fact carries ``valid_from`` / ``valid_until`` windows, entity dedup
happens automatically across ingests, and hybrid search (vector + BM25
+ graph distance) is one call.

We pick FalkorDB over Neo4j as the default backend because: (a) it's a
Redis module so a single Redis container gets us both KV and graph
without operating two databases; (b) its license is friendlier for
opensource self-hosters than Neo4j's GPLv3 community edition.

This module is a thin facade. Subsystems (api/retrieval, librarian,
connectors) talk to ``KnowledgeGraph`` so we can swap the backend
later without touching them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from lighthouse.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GraphSearchHit:
    """One result from ``KnowledgeGraph.search``.

    Decoupled from Graphiti's native shape so the API layer never imports
    from graphiti_core directly — keeps the swap seam clean.
    """

    node_id: str
    summary: str
    score: float
    source_url: str | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None


@dataclass(slots=True)
class GraphNode:
    """One node fetched by id."""

    node_id: str
    content: str
    provenance: dict[str, str]


class KnowledgeGraph:
    """Lazy facade over a Graphiti client.

    The Graphiti import + driver connection is deferred until first call.
    This keeps test boots and ``--help`` invocations fast, and means a
    misconfigured env doesn't break module import (the error surfaces on
    the first real query instead).
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: Any | None = None

    async def _client_lazy(self) -> Any:
        if self._client is not None:
            return self._client
        # Import here so the optional dep doesn't block module import.
        from graphiti_core import Graphiti
        from graphiti_core.driver.falkordb_driver import FalkorDriver

        driver = FalkorDriver(
            host=self._settings.falkordb_host,
            port=self._settings.falkordb_port,
            database=self._settings.falkordb_database,
        )
        self._client = Graphiti(graph_driver=driver)
        return self._client

    async def search(self, query: str, top_k: int = 10) -> list[GraphSearchHit]:
        client = await self._client_lazy()
        raw = await client.search(query=query, num_results=top_k)
        out: list[GraphSearchHit] = []
        for r in raw:
            out.append(
                GraphSearchHit(
                    node_id=str(getattr(r, "uuid", "")),
                    summary=str(getattr(r, "fact", "") or getattr(r, "name", "")),
                    score=float(getattr(r, "score", 0.0)),
                    valid_from=getattr(r, "valid_at", None),
                    valid_until=getattr(r, "invalid_at", None),
                )
            )
        return out

    async def fetch(self, node_id: str) -> GraphNode | None:
        client = await self._client_lazy()
        node = await client.get_node(node_id)
        if node is None:
            return None
        return GraphNode(
            node_id=str(getattr(node, "uuid", node_id)),
            content=str(getattr(node, "summary", "") or getattr(node, "name", "")),
            provenance={
                k: str(v)
                for k, v in (getattr(node, "attributes", {}) or {}).items()
            },
        )

    async def upsert_episode(
        self,
        name: str,
        body: str,
        source: str,
        reference_time: datetime | None = None,
    ) -> None:
        """Feed one episode (a chunk of source text) into the graph.

        Graphiti handles entity extraction, dedup against prior episodes,
        and temporal bookkeeping under the hood.
        """
        client = await self._client_lazy()
        await client.add_episode(
            name=name,
            episode_body=body,
            source_description=source,
            reference_time=reference_time or datetime.utcnow(),
        )
