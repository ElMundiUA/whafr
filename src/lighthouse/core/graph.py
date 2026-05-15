"""Knowledge graph wrapper around Graphiti + FalkorDB.

Graphiti gives us temporal knowledge graph semantics out of the box:
each fact (an :class:`EntityEdge`) carries ``valid_at`` / ``invalid_at``
windows, entity dedup happens automatically across ingests, and hybrid
search (vector + BM25 + cross-encoder rerank + graph BFS) is one call.

We pick FalkorDB over Neo4j as the default backend because: (a) it's a
Redis module so a single container gets us both KV and graph without
operating two databases, and (b) its BSL license is friendlier for
opensource self-hosters than Neo4j's GPLv3 community edition.

This module is a thin facade. Subsystems (api, librarian, connectors)
talk to :class:`KnowledgeGraph` so we can swap the backend or even
mock it in tests without ripping up the call sites.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from lighthouse.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GraphSearchHit:
    """One result from :meth:`KnowledgeGraph.search`.

    Projected from Graphiti's ``EntityEdge`` so the API layer never
    imports from ``graphiti_core`` directly — keeps the swap seam clean.
    The ``summary`` field maps to the edge's ``fact`` (natural-language
    statement of the relationship); ``node_id`` is the edge's UUID.
    """

    node_id: str
    summary: str
    source_node_uuid: str | None = None
    target_node_uuid: str | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GraphNode:
    """One node fetched by uuid via :meth:`KnowledgeGraph.fetch`."""

    node_id: str
    name: str
    summary: str
    labels: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)


class KnowledgeGraph:
    """Lazy facade over a Graphiti client.

    The Graphiti import + driver connection is deferred until first call.
    This keeps test boots and ``--help`` invocations fast, and means a
    misconfigured env doesn't break module import — the error surfaces
    on the first real query instead, where the caller can handle it.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: Any | None = None
        self._driver: Any | None = None

    async def _client_lazy(self) -> Any:
        if self._client is not None:
            return self._client
        # Lazy imports — graphiti and its falkordb extra pull a lot of
        # transitive deps that we don't want to pay for on test boots
        # or quick CLI runs that don't touch the graph.
        from graphiti_core import Graphiti
        from graphiti_core.driver.falkordb_driver import FalkorDriver
        from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
        from graphiti_core.llm_client.config import LLMConfig
        from graphiti_core.llm_client.openai_client import OpenAIClient

        self._driver = FalkorDriver(
            host=self._settings.falkordb_host,
            port=self._settings.falkordb_port,
            database=self._settings.falkordb_database,
        )

        if not self._settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set — Graphiti needs OpenAI for "
                "entity extraction and embeddings. Set it in .env or "
                "the environment before running ingest/search."
            )

        llm_client = OpenAIClient(
            config=LLMConfig(
                api_key=self._settings.openai_api_key,
                model=self._settings.openai_model,
                small_model=self._settings.openai_small_model,
            )
        )
        embedder = OpenAIEmbedder(
            config=OpenAIEmbedderConfig(
                api_key=self._settings.openai_api_key,
                embedding_model=self._settings.openai_embedding_model,
                embedding_dim=self._settings.openai_embedding_dim,
            )
        )

        self._client = Graphiti(
            graph_driver=self._driver,
            llm_client=llm_client,
            embedder=embedder,
        )
        return self._client

    async def initialize(self) -> None:
        """Create the FalkorDB indices and constraints Graphiti expects.

        Safe to call repeatedly — ``delete_existing=False`` makes this
        idempotent. The intended caller is a one-shot bootstrap script
        or the first run of a fresh instance.
        """
        client = await self._client_lazy()
        await client.build_indices_and_constraints(delete_existing=False)

    async def search(self, query: str, top_k: int = 10) -> list[GraphSearchHit]:
        """Run a hybrid (BM25 + vector + BFS) search across the graph.

        Graphiti returns ``EntityEdge`` instances — each edge is a fact
        connecting two entity nodes. We project them flat for the API
        layer; callers that need the full subgraph can chase
        ``source_node_uuid`` / ``target_node_uuid`` through :meth:`fetch`.
        """
        client = await self._client_lazy()
        edges = await client.search(query=query, num_results=top_k)
        out: list[GraphSearchHit] = []
        for e in edges:
            out.append(
                GraphSearchHit(
                    node_id=str(e.uuid),
                    summary=str(getattr(e, "fact", "") or getattr(e, "name", "")),
                    source_node_uuid=str(getattr(e, "source_node_uuid", "") or ""),
                    target_node_uuid=str(getattr(e, "target_node_uuid", "") or ""),
                    valid_from=getattr(e, "valid_at", None),
                    valid_until=getattr(e, "invalid_at", None),
                    attributes=dict(getattr(e, "attributes", {}) or {}),
                )
            )
        return out

    async def fetch(self, node_id: str) -> GraphNode | None:
        """Look up one entity node by uuid.

        Graphiti exposes node lookups through the driver rather than a
        helper on the client, so we issue a parameterised Cypher query
        directly. The ``query_executor`` interface is stable across the
        backends Graphiti supports (FalkorDB, Neo4j, Kuzu, Neptune).
        """
        client = await self._client_lazy()
        driver = self._driver
        if driver is None:  # pragma: no cover — set in _client_lazy
            return None

        # Both Entity and Episodic nodes carry a ``uuid`` property; we
        # only return Entity here because Episodic nodes are raw source
        # chunks the API doesn't surface as standalone records.
        async with driver.session() as session:
            result = await session.run(
                "MATCH (n:Entity {uuid: $uuid}) "
                "RETURN n.uuid AS uuid, n.name AS name, n.summary AS summary, "
                "       labels(n) AS labels, n.attributes AS attributes "
                "LIMIT 1",
                uuid=node_id,
            )
            records = await result.values() if hasattr(result, "values") else result
            row = records[0] if records else None

        if row is None:
            return None

        # Driver row shape varies slightly across backends; coerce to a
        # dict so we don't bind to FalkorDB's tuple layout specifically.
        if isinstance(row, dict):
            uuid_v = row.get("uuid") or node_id
            name = row.get("name") or ""
            summary = row.get("summary") or ""
            labels = list(row.get("labels") or [])
            attrs = dict(row.get("attributes") or {})
        else:  # list/tuple
            uuid_v, name, summary, labels, attrs = (list(row) + [None] * 5)[:5]
        return GraphNode(
            node_id=str(uuid_v or node_id),
            name=str(name or ""),
            summary=str(summary or ""),
            labels=list(labels or []),
            attributes=dict(attrs or {}),
        )

    async def upsert_episode(
        self,
        *,
        name: str,
        body: str,
        source: str,
        reference_time: datetime | None = None,
        group_id: str = "lighthouse",
    ) -> str:
        """Feed one episode (a chunk of source text) into the graph.

        Graphiti handles entity extraction, dedup against prior episodes,
        and temporal bookkeeping under the hood. Returns the episode's
        UUID so the caller can correlate ingest provenance later.

        ``group_id`` partitions the graph. Default is ``"lighthouse"`` —
        Graphiti requires a non-empty alphanumeric/dash/underscore id
        and rejects empty or special-character defaults. Callers wanting
        per-tenant or per-deployment isolation pass their own id.
        """
        client = await self._client_lazy()
        result = await client.add_episode(
            name=name,
            episode_body=body,
            source_description=source,
            reference_time=reference_time or datetime.now(UTC),
            group_id=group_id,
        )
        return str(getattr(result, "episode_uuid", "") or getattr(result, "uuid", ""))

    async def close(self) -> None:
        """Release the underlying driver connection.

        Safe to call when nothing was opened. The FastAPI shutdown hook
        wires this up so reload-loops in dev don't leak Redis sockets.
        """
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                logger.exception("error closing graphiti client")
            self._client = None
            self._driver = None
