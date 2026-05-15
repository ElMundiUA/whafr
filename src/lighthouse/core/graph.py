"""Knowledge graph wrapper around Graphiti + Neo4j.

Graphiti gives us temporal knowledge graph semantics out of the box:
each fact (an :class:`EntityEdge`) carries ``valid_at`` / ``invalid_at``
windows, entity dedup happens automatically across ingests, and hybrid
search (vector + BM25 + cross-encoder rerank) is one call.

Backend is Neo4j 5.26 Community Edition (GPLv3) — we moved off FalkorDB
in v0.2 because its BSL ("Business Source License") is source-available,
not OSS, and that's a problem for an opensource project. Neo4j CE is
a touch heavier to operate (separate process, JVM) but its license is
clean for self-hosters and managed offerings.

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

    ``episode_ids`` carries the UUID(s) of the Episodic nodes this fact
    was extracted from — feed any of them into
    :meth:`KnowledgeGraph.fetch_source` to read the original ingested
    chunk instead of paying for N more entity drill-ins.
    """

    node_id: str
    summary: str
    source_node_uuid: str | None = None
    target_node_uuid: str | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    episode_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class GraphSource:
    """One Episodic node — the raw source chunk an edge was extracted from.

    Returned by :meth:`KnowledgeGraph.fetch_source`. Agents read this
    when the short fact summary from search isn't enough and they want
    the surrounding paragraphs without doing N entity fetches.
    """

    episode_id: str
    name: str
    source: str  # ``<connector>:<url>`` as we record it at ingest
    content: str
    created_at: datetime | None = None
    valid_at: datetime | None = None


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
        # Lazy imports — graphiti and its neo4j extra pull a lot of
        # transitive deps that we don't want to pay for on test boots
        # or quick CLI runs that don't touch the graph.
        from graphiti_core import Graphiti
        from graphiti_core.cross_encoder.openai_reranker_client import (
            OpenAIRerankerClient,
        )
        from graphiti_core.driver.neo4j_driver import Neo4jDriver
        from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
        from graphiti_core.llm_client.config import LLMConfig
        from graphiti_core.llm_client.openai_client import OpenAIClient

        self._driver = Neo4jDriver(
            uri=self._settings.neo4j_uri,
            user=self._settings.neo4j_user,
            password=self._settings.neo4j_password,
            database=self._settings.neo4j_database,
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

        # Wire a cross-encoder so we can use Graphiti's CROSS_ENCODER
        # search recipe. Without one, falling back to RRF gives much
        # noisier hits (lexical false-positives dominate top_k).
        reranker = OpenAIRerankerClient(
            config=LLMConfig(
                api_key=self._settings.openai_api_key,
                model=self._settings.openai_small_model,
                small_model=self._settings.openai_small_model,
            )
        )

        self._client = Graphiti(
            graph_driver=self._driver,
            llm_client=llm_client,
            embedder=embedder,
            cross_encoder=reranker,
        )
        return self._client

    async def initialize(self) -> None:
        """Create the Neo4j indices and constraints Graphiti expects.

        Safe to call repeatedly — ``delete_existing=False`` makes this
        idempotent. The intended caller is a one-shot bootstrap script
        or the first run of a fresh instance.
        """
        client = await self._client_lazy()
        await client.build_indices_and_constraints(delete_existing=False)

    async def search(self, query: str, top_k: int = 10) -> list[GraphSearchHit]:
        """Run a hybrid (BM25 + vector + cross-encoder rerank) search.

        Uses Graphiti's ``EDGE_HYBRID_SEARCH_CROSS_ENCODER`` recipe (a
        copy with our own ``limit``) instead of the default convenience
        path, which falls back to RRF when no cross-encoder is wired and
        produces noisier top_k. We oversample ``MIN_OVERSAMPLE`` × top_k
        candidates, let the cross-encoder rerank, then post-filter:

        - drop summaries shorter than ``MIN_SUMMARY_CHARS`` (one-word
          declarations like "X was discussed" hurt more than help);
        - dedupe by normalized first-80-chars (Graphiti can return the
          same fact under two edge uuids when a source has been ingested
          twice).

        Callers that need the full subgraph chase ``source_node_uuid`` /
        ``target_node_uuid`` through :meth:`fetch`.
        """
        from copy import deepcopy

        from graphiti_core.search.search_config_recipes import (
            EDGE_HYBRID_SEARCH_CROSS_ENCODER,
        )

        MIN_SUMMARY_CHARS = 40
        MIN_OVERSAMPLE = 3

        client = await self._client_lazy()
        cfg = deepcopy(EDGE_HYBRID_SEARCH_CROSS_ENCODER)
        cfg.limit = max(top_k * MIN_OVERSAMPLE, 20)
        # Strip BFS — it issues O(n²) Cypher that times out on FalkorDB
        # once the graph gets past a few thousand edges. BM25 + cosine
        # + cross-encoder rerank covers the same recall surface for our
        # bench tasks without the latency cliff.
        try:
            from graphiti_core.search.search_config import EdgeSearchMethod

            cfg.edge_config.search_methods = [
                m
                for m in cfg.edge_config.search_methods
                if m != EdgeSearchMethod.bfs
            ]
        except Exception:  # pragma: no cover — keep working if import shape shifts
            pass
        try:
            results = await client._search(query=query, config=cfg)
            edges = list(getattr(results, "edges", None) or [])
        except Exception:
            # Cross-encoder path can fail (e.g. quota), fall back to
            # the convenience search rather than 500 the request.
            logger.warning("cross-encoder search failed, falling back to RRF", exc_info=True)
            edges = await client.search(query=query, num_results=top_k * MIN_OVERSAMPLE)

        def _project(e) -> GraphSearchHit:
            eps_raw = getattr(e, "episodes", None) or []
            episode_ids = [str(x) for x in eps_raw if x]
            return GraphSearchHit(
                node_id=str(e.uuid),
                summary=str(getattr(e, "fact", "") or getattr(e, "name", "") or "").strip(),
                source_node_uuid=str(getattr(e, "source_node_uuid", "") or ""),
                target_node_uuid=str(getattr(e, "target_node_uuid", "") or ""),
                valid_from=getattr(e, "valid_at", None),
                valid_until=getattr(e, "invalid_at", None),
                attributes=dict(getattr(e, "attributes", {}) or {}),
                episode_ids=episode_ids,
            )

        out: list[GraphSearchHit] = []
        seen_keys: set[str] = set()
        for e in edges:
            hit = _project(e)
            if len(hit.summary) < MIN_SUMMARY_CHARS:
                continue
            key = " ".join(hit.summary.lower().split())[:80]
            if key in seen_keys:
                continue
            seen_keys.add(key)
            out.append(hit)
            if len(out) >= top_k:
                break

        # Fallback: if the filter wiped everything (e.g. all candidates
        # were one-liners), return up to top_k of the raw reranked list
        # rather than an empty array. Empty results are worse than
        # noisy ones — the agent loses retrieval *and* has no chance
        # to evaluate relevance itself.
        if not out and edges:
            for e in edges[:top_k]:
                hit = _project(e)
                if hit.summary:
                    out.append(hit)
        return out

    async def fetch(self, node_id: str) -> GraphNode | None:
        """Look up one entity node by uuid.

        Uses the Neo4j async driver directly (parameter-bound Cypher).
        """
        from neo4j import AsyncGraphDatabase

        driver = AsyncGraphDatabase.driver(
            self._settings.neo4j_uri,
            auth=(self._settings.neo4j_user, self._settings.neo4j_password),
        )
        try:
            async with driver.session(database=self._settings.neo4j_database) as session:
                result = await session.run(
                    "MATCH (n:Entity {uuid: $uuid}) "
                    "RETURN n.uuid AS uuid, n.name AS name, "
                    "n.summary AS summary, labels(n) AS labels LIMIT 1",
                    uuid=node_id,
                )
                row = await result.single()
        finally:
            await driver.close()
        if row is None:
            return None
        return GraphNode(
            node_id=str(row["uuid"] or node_id),
            name=str(row["name"] or ""),
            summary=str(row["summary"] or ""),
            labels=list(row["labels"] or []),
            attributes={},
        )

    async def fetch_source(self, episode_id: str) -> GraphSource | None:
        """Return the raw Episodic chunk a search hit was extracted from.

        Agents use this to cut round-trips: instead of N ``fetch`` calls
        chasing the entities a fact relates, they pull the source
        paragraph once and read it directly. Returns ``None`` if the id
        doesn't match an Episodic node (it might be an Entity uuid by
        mistake — callers should distinguish).
        """
        from neo4j import AsyncGraphDatabase
        from neo4j.time import DateTime as Neo4jDateTime

        driver = AsyncGraphDatabase.driver(
            self._settings.neo4j_uri,
            auth=(self._settings.neo4j_user, self._settings.neo4j_password),
        )
        try:
            async with driver.session(database=self._settings.neo4j_database) as session:
                result = await session.run(
                    "MATCH (n:Episodic {uuid: $uuid}) "
                    "RETURN n.uuid AS uuid, n.name AS name, "
                    "n.source_description AS source, n.content AS content, "
                    "n.created_at AS created_at, n.valid_at AS valid_at LIMIT 1",
                    uuid=episode_id,
                )
                row = await result.single()
        finally:
            await driver.close()
        if row is None:
            return None

        def _to_dt(v):
            if v is None:
                return None
            if isinstance(v, Neo4jDateTime):
                return v.to_native()
            if isinstance(v, str):
                try:
                    return datetime.fromisoformat(v.replace("Z", "+00:00"))
                except ValueError:
                    return None
            return None

        return GraphSource(
            episode_id=str(row["uuid"] or episode_id),
            name=str(row["name"] or ""),
            source=str(row["source"] or ""),
            content=str(row["content"] or ""),
            created_at=_to_dt(row["created_at"]),
            valid_at=_to_dt(row["valid_at"]),
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
        # Graphiti's AddEpisodeResults shape — episode is nested.
        # Fall back to legacy fields for older versions.
        episode = getattr(result, "episode", None)
        uuid = getattr(episode, "uuid", None) if episode is not None else None
        return str(
            uuid
            or getattr(result, "episode_uuid", "")
            or getattr(result, "uuid", "")
        )

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
