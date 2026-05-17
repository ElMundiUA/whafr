"""Flat-RAG graph adapter.

A deliberately small alternative to :class:`KnowledgeGraph` that drops
Graphiti's entity-relation extraction layer and keeps only what
audit-evidence shows is actually load-bearing for retrieval:

- one :Chunk node per ingested document chunk
- fulltext index over ``content`` (the dominant retrieval signal we
  observed via the episode-body second pass)
- vector index over ``embedding`` (cosine similarity for semantic
  proximity)
- ``published_at`` + optional ``version`` + optional ``superseded_by``
  for time-aware filtering — time facts at chunk granularity,
  the layer Graphiti's edge-level temporal invalidation never
  meaningfully delivered for us.

What this **doesn't** do (deliberate):
- No entity extraction. Each ingest pass embeds the body and indexes
  it; no LLM call to enumerate entities/relations.
- No cross-chunk dedup at extraction time. The chunk uuid is derived
  from ``source_id`` + content hash so re-running an ingest is
  idempotent without LLM-side dedup gymnastics.
- No "communities" / BFS / saga search recipes. Practical hybrid
  retrieval (BM25 + vector + optional cross-encoder rerank) covers
  every query shape the bench actually uses.

Runs in the same Neo4j instance as the Graphiti corpus — the
``Chunk`` label keeps it disjoint from ``Episodic``/``Entity`` so the
two coexist during A/B comparison.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from lighthouse.core.config import Settings, get_settings
from lighthouse.core.graph import _split_episode_body

logger = logging.getLogger(__name__)


# Same default cap as the Graphiti path (fits comfortably in any
# embedder's token budget). Re-exported for callers that want to size
# their connectors symmetrically.
MAX_CHUNK_CHARS = 12000


# Search-side knobs. Settled by spike-testing against the existing
# canonical queries; tuned more once we have an A/B audit run.
DEFAULT_TOP_K = 10
BM25_OVERSAMPLE = 3   # fetch 3× before rerank
VECTOR_OVERSAMPLE = 3
RERANKER_MIN_SCORE = 0.001  # matches the Graphiti path's floor


@dataclass(slots=True)
class FlatHit:
    """One search hit. Same shape as
    :class:`lighthouse.core.graph.GraphSearchHit` so the MCP layer can
    project both paths identically."""

    node_id: str
    summary: str
    source: str
    url: str | None = None
    published_at: datetime | None = None
    score: float = 0.0
    episode_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class FlatChunk:
    """One ingested chunk — the unit of retrieval + the unit of
    cost. ``content_sha256`` is the delta-skip key (matches the
    Graphiti path's ``lighthouse_full_body_sha256``)."""

    chunk_id: str
    name: str
    source: str
    content: str
    content_sha256: str
    url: str | None = None
    published_at: datetime | None = None
    version: str | None = None


class FlatGraph:
    """Flat-RAG facade. Same lifecycle as :class:`KnowledgeGraph` so
    the runner / CLI can pick which engine to use behind a settings
    flag."""

    LABEL = "Chunk"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._initialized = False

    async def initialize(self) -> None:
        """Idempotent — creates indexes if they don't exist."""
        if self._initialized:
            return
        from neo4j import AsyncGraphDatabase

        s = self._settings
        driver = AsyncGraphDatabase.driver(
            s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password)
        )
        try:
            async with driver.session(database=s.neo4j_database) as session:
                # Fulltext index over content + title. Title is short
                # but high-signal — e.g. "RFC 7636: PKCE" — and BM25
                # naturally weights short rare-token fields well.
                await session.run(
                    "CREATE FULLTEXT INDEX flat_chunk_content IF NOT EXISTS "
                    "FOR (c:Chunk) ON EACH [c.content, c.name]"
                )
                # Vector index — Neo4j 5.13+. Cosine similarity is the
                # standard for embeddings.
                await session.run(
                    "CREATE VECTOR INDEX flat_chunk_embedding IF NOT EXISTS "
                    "FOR (c:Chunk) ON c.embedding "
                    "OPTIONS { indexConfig: { "
                    "  `vector.dimensions`: $dim, "
                    "  `vector.similarity_function`: 'cosine' "
                    "}}",
                    dim=int(s.openai_embedding_dim),
                )
                await session.run(
                    "CREATE INDEX flat_chunk_uuid IF NOT EXISTS "
                    "FOR (c:Chunk) ON (c.uuid)"
                )
                await session.run(
                    "CREATE INDEX flat_chunk_source_published IF NOT EXISTS "
                    "FOR (c:Chunk) ON (c.source, c.published_at)"
                )
                await session.run(
                    "CREATE INDEX flat_chunk_sha IF NOT EXISTS "
                    "FOR (c:Chunk) ON (c.content_sha256)"
                )
        finally:
            await driver.close()
        self._initialized = True

    # ---- write path -----------------------------------------------------

    async def has_unchanged_chunk(self, source: str, body_sha256: str) -> bool:
        """Delta-skip pre-check — does an existing Chunk for this
        source carry the same full-body hash? Same semantics as
        :meth:`KnowledgeGraph.has_unchanged_episode`."""
        from neo4j import AsyncGraphDatabase

        s = self._settings
        driver = AsyncGraphDatabase.driver(
            s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password)
        )
        try:
            async with driver.session(database=s.neo4j_database) as session:
                result = await session.run(
                    "MATCH (c:Chunk) "
                    "WHERE c.source = $src "
                    "AND c.full_body_sha256 = $hash "
                    "RETURN 1 LIMIT 1",
                    src=source,
                    hash=body_sha256,
                )
                row = await result.single()
                return row is not None
        finally:
            await driver.close()

    async def upsert_document(
        self,
        *,
        name: str,
        body: str,
        source: str,
        reference_time: datetime | None = None,
        url: str | None = None,
        version: str | None = None,
    ) -> str:
        """Splits the body into chunks, embeds each, upserts as :Chunk
        nodes. Returns the uuid of the first chunk for caller
        bookkeeping."""
        if not body or not body.strip():
            return ""
        ref = reference_time or datetime.now(UTC)
        full_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        chunks = _split_episode_body(body, cap=MAX_CHUNK_CHARS)

        embeddings = await self._embed_batch(
            [chunk for chunk in chunks]
        )

        rows: list[dict[str, Any]] = []
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings, strict=True)):
            chunk_name = (
                name if len(chunks) == 1 else f"{name} (part {i + 1}/{len(chunks)})"
            )
            chunk_uuid = _deterministic_uuid(source, full_hash, i)
            rows.append(
                {
                    "uuid": chunk_uuid,
                    "name": chunk_name,
                    "source": source,
                    "content": chunk,
                    "content_sha256": hashlib.sha256(
                        chunk.encode("utf-8")
                    ).hexdigest(),
                    "full_body_sha256": full_hash,
                    "url": url,
                    "published_at": ref.isoformat(),
                    "ingested_at": datetime.now(UTC).isoformat(),
                    "version": version,
                    "embedding": emb,
                    "chunk_index": i,
                    "chunk_count": len(chunks),
                }
            )

        from neo4j import AsyncGraphDatabase

        s = self._settings
        driver = AsyncGraphDatabase.driver(
            s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password)
        )
        try:
            async with driver.session(database=s.neo4j_database) as session:
                # MERGE on uuid so re-ingest of an exact chunk no-ops
                # at the Neo4j layer too (defence in depth — delta-skip
                # should catch most before we get here).
                await session.run(
                    """
                    UNWIND $rows AS row
                    MERGE (c:Chunk {uuid: row.uuid})
                    SET c.name = row.name,
                        c.source = row.source,
                        c.content = row.content,
                        c.content_sha256 = row.content_sha256,
                        c.full_body_sha256 = row.full_body_sha256,
                        c.url = row.url,
                        c.published_at = datetime(row.published_at),
                        c.ingested_at = datetime(row.ingested_at),
                        c.version = row.version,
                        c.embedding = row.embedding,
                        c.chunk_index = row.chunk_index,
                        c.chunk_count = row.chunk_count
                    """,
                    rows=rows,
                )
        finally:
            await driver.close()
        return rows[0]["uuid"]

    # ---- read path ------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        top_k: int = DEFAULT_TOP_K,
        after: datetime | None = None,
        before: datetime | None = None,
        version: str | None = None,
        include_release_notes: bool | None = None,
    ) -> list[FlatHit]:
        """Hybrid BM25 + vector search with time-aware filtering.

        ``after`` / ``before`` filter by ``published_at``. Pass
        ``after=<frontier_cutoff>`` to surface only post-cutoff
        material — the canonical "what frontier models don't have"
        slice.

        ``include_release_notes`` overrides the heuristic that
        excludes ``gh-releases-*``/``rss-*`` sources on how-to queries
        (same heuristic as :meth:`KnowledgeGraph._search_episodes`).
        """
        if not query or not query.strip():
            return []
        if include_release_notes is None:
            include_release_notes = _query_wants_releases(query)
        excluded_prefixes: list[str] = (
            [] if include_release_notes else ["gh-releases-", "rss-"]
        )

        # Lucene-safe query — same shape as the Graphiti path.
        safe_q = "".join(
            c if c.isalnum() or c in " -_" else " " for c in query
        ).strip()
        if not safe_q:
            return []

        # Embed the query once so the vector branch and any caller-side
        # reranker share the same vector.
        q_embedding = (await self._embed_batch([query]))[0]

        bm25 = await self._search_bm25(
            safe_q,
            limit=top_k * BM25_OVERSAMPLE,
            after=after,
            before=before,
            version=version,
            excluded_prefixes=excluded_prefixes,
        )
        vec = await self._search_vector(
            q_embedding,
            limit=top_k * VECTOR_OVERSAMPLE,
            after=after,
            before=before,
            version=version,
            excluded_prefixes=excluded_prefixes,
        )

        # Rank fusion: reciprocal rank fusion with BM25 weighted
        # slightly higher (BM25 has been the stronger signal in audit
        # evidence). k=60 is the canonical RRF constant.
        scored: dict[str, tuple[float, FlatHit]] = {}
        for rank, h in enumerate(bm25):
            scored[h.node_id] = (1.0 / (60 + rank) * 1.2, h)
        for rank, h in enumerate(vec):
            prior = scored.get(h.node_id, (0.0, h))
            new_score = prior[0] + 1.0 / (60 + rank)
            scored[h.node_id] = (new_score, prior[1] if prior[0] else h)

        merged = sorted(
            scored.values(), key=lambda kv: kv[0], reverse=True
        )[: top_k * 2]
        out: list[FlatHit] = []
        seen_keys: set[str] = set()
        for score, hit in merged:
            key = " ".join((hit.summary or "").lower().split())[:80]
            if key in seen_keys:
                continue
            seen_keys.add(key)
            hit.score = score
            out.append(hit)
            if len(out) >= top_k:
                break
        return out

    async def fetch_source(
        self, chunk_id: str, *, max_chars: int = 6000
    ) -> dict[str, Any] | None:
        """Return the chunk body, capped to ``max_chars``. Same shape
        the MCP ``fetch_source`` tool already serialises."""
        from neo4j import AsyncGraphDatabase

        s = self._settings
        driver = AsyncGraphDatabase.driver(
            s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password)
        )
        try:
            async with driver.session(database=s.neo4j_database) as session:
                result = await session.run(
                    "MATCH (c:Chunk {uuid: $uuid}) "
                    "RETURN c.uuid AS uuid, c.name AS name, "
                    "c.source AS source, c.url AS url, c.content AS content, "
                    "c.published_at AS published_at "
                    "LIMIT 1",
                    uuid=chunk_id,
                )
                row = await result.single()
        finally:
            await driver.close()
        if row is None:
            return None
        cap = max(200, min(int(max_chars), 20000))
        body = row["content"] or ""
        truncated = len(body) > cap
        if truncated:
            body = body[:cap]
        return {
            "episode_id": row["uuid"],
            "name": row["name"],
            "source": row["source"],
            "url": row["url"],
            "content": body,
            "truncated": truncated,
            "full_length": len(row["content"] or ""),
            "valid_at": (
                row["published_at"].isoformat()
                if row["published_at"] is not None
                else None
            ),
        }

    # ---- internals ------------------------------------------------------

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Batch embedding via OpenAI. Bulk-API call — one request per
        chunk batch instead of per-chunk, which is the dominant cost
        win we get from owning this path."""
        if not texts:
            return []
        from openai import AsyncOpenAI

        s = self._settings
        client = AsyncOpenAI(api_key=s.openai_api_key)
        # OpenAI accepts up to 2048 inputs per call. Our chunks fit
        # well inside that with margin. ``dimensions`` is critical —
        # without it text-embedding-3-small returns 1536-d vectors,
        # but the existing Neo4j vector index was created with
        # ``openai_embedding_dim`` (default 1024) to match Graphiti's
        # bytes-on-disk budget. Mismatched dims fail the index call.
        resp = await client.embeddings.create(
            model=s.openai_embedding_model,
            input=texts,
            dimensions=int(s.openai_embedding_dim),
        )
        return [d.embedding for d in resp.data]

    async def _search_bm25(
        self,
        safe_q: str,
        *,
        limit: int,
        after: datetime | None,
        before: datetime | None,
        version: str | None,
        excluded_prefixes: list[str],
    ) -> list[FlatHit]:
        from neo4j import AsyncGraphDatabase

        s = self._settings
        driver = AsyncGraphDatabase.driver(
            s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password)
        )
        where = ["node:Chunk"]
        params: dict[str, Any] = {
            "idx": "flat_chunk_content",
            "q": safe_q,
            "limit": int(limit),
        }
        if after is not None:
            where.append("node.published_at >= datetime($after)")
            params["after"] = after.isoformat()
        if before is not None:
            where.append("node.published_at < datetime($before)")
            params["before"] = before.isoformat()
        if version is not None:
            where.append("node.version = $version")
            params["version"] = version
        if excluded_prefixes:
            where.append(
                "NOT any(p IN $excluded WHERE node.source STARTS WITH p)"
            )
            params["excluded"] = excluded_prefixes
        try:
            async with driver.session(database=s.neo4j_database) as session:
                result = await session.run(
                    "CALL db.index.fulltext.queryNodes($idx, $q) "
                    "YIELD node, score "
                    "WHERE " + " AND ".join(where) + " "
                    "RETURN node.uuid AS uuid, node.name AS name, "
                    "node.source AS source, node.url AS url, "
                    "node.content AS content, "
                    "node.published_at AS published_at, score "
                    "ORDER BY score DESC LIMIT $limit",
                    **params,
                )
                rows = [r async for r in result]
        finally:
            await driver.close()
        return [_row_to_hit(r) for r in rows]

    async def _search_vector(
        self,
        embedding: list[float],
        *,
        limit: int,
        after: datetime | None,
        before: datetime | None,
        version: str | None,
        excluded_prefixes: list[str],
    ) -> list[FlatHit]:
        from neo4j import AsyncGraphDatabase

        s = self._settings
        driver = AsyncGraphDatabase.driver(
            s.neo4j_uri, auth=(s.neo4j_user, s.neo4j_password)
        )
        where = ["node:Chunk"]
        params: dict[str, Any] = {
            "embedding": embedding,
            "limit": int(limit),
            "idx": "flat_chunk_embedding",
            # Oversample inside the vector index since the WHERE clause
            # is applied post-search.
            "k": int(limit) * 3,
        }
        if after is not None:
            where.append("node.published_at >= datetime($after)")
            params["after"] = after.isoformat()
        if before is not None:
            where.append("node.published_at < datetime($before)")
            params["before"] = before.isoformat()
        if version is not None:
            where.append("node.version = $version")
            params["version"] = version
        if excluded_prefixes:
            where.append(
                "NOT any(p IN $excluded WHERE node.source STARTS WITH p)"
            )
            params["excluded"] = excluded_prefixes
        try:
            async with driver.session(database=s.neo4j_database) as session:
                result = await session.run(
                    "CALL db.index.vector.queryNodes($idx, $k, $embedding) "
                    "YIELD node, score "
                    "WHERE " + " AND ".join(where) + " "
                    "RETURN node.uuid AS uuid, node.name AS name, "
                    "node.source AS source, node.url AS url, "
                    "node.content AS content, "
                    "node.published_at AS published_at, score "
                    "ORDER BY score DESC LIMIT $limit",
                    **params,
                )
                rows = [r async for r in result]
        finally:
            await driver.close()
        return [_row_to_hit(r) for r in rows]


def _row_to_hit(row: Any) -> FlatHit:
    content = (row["content"] or "").strip()
    snippet = content[:280] + ("…" if len(content) > 280 else "")
    name = row["name"] or ""
    summary = f"# {name}\n{snippet}" if name else snippet
    pub = row.get("published_at") if hasattr(row, "get") else row["published_at"]
    if pub is not None and not isinstance(pub, datetime):
        try:
            pub = pub.to_native()  # type: ignore[attr-defined]
        except AttributeError:
            pub = None
    return FlatHit(
        node_id=str(row["uuid"] or ""),
        summary=summary,
        source=str(row["source"] or ""),
        url=str(row["url"] or "") or None,
        published_at=pub,
        score=float(row.get("score", 0.0)) if hasattr(row, "get") else float(row["score"]),
        episode_ids=[str(row["uuid"] or "")],
    )


def _deterministic_uuid(source: str, full_hash: str, chunk_index: int) -> str:
    """Stable uuid for re-ingest idempotency. Derived from the
    source key + content hash + chunk index so the same body always
    yields the same uuid, and a changed body yields a fresh one
    (which the MERGE will treat as a new chunk; orphan-cleanup is a
    follow-up task)."""
    base = f"{source}:{full_hash}:{chunk_index}"
    return str(_uuid.UUID(hashlib.md5(base.encode("utf-8")).hexdigest()))


_RELEASE_KW_RE = re.compile(
    r"what'?s new|whats new|what is new|release note|changelog|"
    r"since version|added in v|deprecate"
)
_VERSION_TOKEN_RE = re.compile(r"\bv\d{1,2}(\.\d+)*\b", re.IGNORECASE)


def _query_wants_releases(query: str) -> bool:
    ql = query.lower()
    if _RELEASE_KW_RE.search(ql):
        return True
    if _VERSION_TOKEN_RE.search(ql):
        return True
    return False
