"""Flat-RAG graph adapter on Postgres + pgvector.

A deliberately small alternative to :class:`KnowledgeGraph` that drops
Graphiti's entity-relation extraction layer and keeps only what
audit-evidence shows is actually load-bearing for retrieval:

- one ``chunks`` row per ingested document chunk
- ``tsvector`` GIN index over ``name || content`` (BM25-ish via
  ``ts_rank_cd``)
- ``vector(N)`` HNSW index over ``embedding`` (cosine)
- ``published_at`` + optional ``version`` + optional
  ``superseded_by`` for time-aware filtering — chunk-level
  time-facts the Graphiti edge-level temporal invalidation never
  meaningfully delivered for us.

What this **doesn't** do (deliberate):
- No entity extraction. Each ingest pass embeds the body and
  indexes it; no LLM call to enumerate entities/relations.
- No cross-chunk dedup at extraction time. The chunk uuid is
  derived from ``source_id`` + content hash so re-running an ingest
  is idempotent without LLM-side gymnastics.
- No "communities" / BFS / saga search recipes. Practical hybrid
  retrieval (BM25 + vector + RRF fusion) covers every query shape
  the bench actually uses.

Lives in a separate Neon project — does not share a database with
Ship or the Graphiti corpus. Switch between engines by env var;
both can run simultaneously during the A/B comparison.
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
# embedder's token budget). Re-exported for callers that want to
# size their connectors symmetrically.
MAX_CHUNK_CHARS = 12000

# Search-side knobs. Settled by spike-testing against the existing
# canonical queries; tuned more once we have an A/B audit run.
DEFAULT_TOP_K = 10
BM25_OVERSAMPLE = 3
VECTOR_OVERSAMPLE = 3


@dataclass(slots=True)
class FlatHit:
    """One search hit. Same shape as
    :class:`lighthouse.core.graph.GraphSearchHit` so the MCP layer
    can project both paths identically."""

    node_id: str
    summary: str
    source: str
    url: str | None = None
    published_at: datetime | None = None
    score: float = 0.0
    episode_ids: list[str] = field(default_factory=list)


class FlatGraph:
    """Flat-RAG facade. Same lifecycle as :class:`KnowledgeGraph`
    so the runner / CLI can pick which engine to use behind a
    settings flag."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._initialized = False
        self._pool: Any | None = None

    async def _pool_lazy(self) -> Any:
        """Lazy connection pool — opened on first use."""
        if self._pool is not None:
            return self._pool
        import asyncpg

        url = self._settings.lighthouse_pg_url
        if not url:
            raise RuntimeError(
                "LIGHTHOUSE_PG_URL is empty — set it to a Postgres "
                "connection string (Neon recommended) to use the "
                "flat-RAG engine. The Graphiti path keeps using "
                "NEO4J_* and is unaffected."
            )
        # Neon's pooler URL carries query parameters asyncpg won't
        # accept (channel_binding etc). Strip them safely.
        clean_url = _strip_neon_extras(url)
        self._pool = await asyncpg.create_pool(
            dsn=clean_url,
            min_size=1,
            max_size=10,
            command_timeout=60,
            statement_cache_size=0,  # pgbouncer-friendly
        )
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def initialize(self) -> None:
        """Idempotent — creates schema + indexes if missing.

        Also evolves the schema: adds ``summary`` / ``tags`` columns
        and a boosted tsvector when missing, so existing deployments
        don't need a manual migration. The boosted tsv is what
        ``search(use_summary_boost=True)`` queries against — kept as
        a separate column so the un-boosted retrieval still works
        unchanged for clients that didn't opt in.
        """
        if self._initialized:
            return
        pool = await self._pool_lazy()
        dim = int(self._settings.openai_embedding_dim)
        async with pool.acquire() as conn:
            # Extensions
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            # Table — column types chosen for cheap reads:
            # `tsv` is a generated tsvector (no triggers).
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS chunks (
                    uuid             UUID PRIMARY KEY,
                    name             TEXT,
                    source           TEXT NOT NULL,
                    url              TEXT,
                    content          TEXT NOT NULL,
                    content_sha256   TEXT NOT NULL,
                    full_body_sha256 TEXT,
                    published_at     TIMESTAMPTZ,
                    ingested_at      TIMESTAMPTZ DEFAULT now(),
                    version          TEXT,
                    superseded_by    UUID REFERENCES chunks(uuid)
                                     ON DELETE SET NULL,
                    chunk_index      INTEGER,
                    chunk_count      INTEGER,
                    embedding        vector({dim}),
                    summary          TEXT,
                    tags             TEXT,
                    tsv              tsvector
                                     GENERATED ALWAYS AS (
                                         setweight(
                                             to_tsvector('english',
                                                 coalesce(name,'')),
                                             'A')
                                         || setweight(
                                             to_tsvector('english',
                                                 coalesce(content,'')),
                                             'B')
                                     ) STORED,
                    tsv_boosted      tsvector
                                     GENERATED ALWAYS AS (
                                         setweight(
                                             to_tsvector('english',
                                                 coalesce(summary,'')),
                                             'A')
                                         || setweight(
                                             to_tsvector('english',
                                                 coalesce(tags,'')),
                                             'A')
                                         || setweight(
                                             to_tsvector('english',
                                                 coalesce(name,'')),
                                             'B')
                                         || setweight(
                                             to_tsvector('english',
                                                 coalesce(content,'')),
                                             'C')
                                     ) STORED
                )
                """
            )
            # Schema-evolve for existing deployments that pre-date
            # the summary/keywords columns. ADD COLUMN IF NOT EXISTS
            # is cheap, idempotent, and matches our test environment.
            # ``keywords`` is the expansion field — synonyms / related
            # terms / alternative phrasings the body itself doesn't
            # mention but a real query might. We weight it the same as
            # summary in the boosted tsvector so it can rescue queries
            # that don't textually match the source.
            for stmt in (
                "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS summary TEXT",
                "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS tags TEXT",
                "ALTER TABLE chunks ADD COLUMN IF NOT EXISTS keywords TEXT",
                # ADD COLUMN with GENERATED clause is supported in
                # PG 12+. Drop + recreate the boosted index whenever
                # the underlying columns shape changes — keeps a
                # single canonical definition in code.
                "ALTER TABLE chunks DROP COLUMN IF EXISTS tsv_boosted",
                """
                ALTER TABLE chunks
                ADD COLUMN IF NOT EXISTS tsv_boosted tsvector
                GENERATED ALWAYS AS (
                    setweight(to_tsvector('english', coalesce(summary,'')), 'A') ||
                    setweight(to_tsvector('english', coalesce(keywords,'')), 'A') ||
                    setweight(to_tsvector('english', coalesce(tags,'')), 'A') ||
                    setweight(to_tsvector('english', coalesce(name,'')), 'B') ||
                    setweight(to_tsvector('english', coalesce(content,'')), 'C')
                ) STORED
                """,
            ):
                try:
                    await conn.execute(stmt)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("schema evolve skipped: %s", exc)
            # Indexes — guarded with IF NOT EXISTS so initialize() is
            # idempotent across deploys.
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON chunks "
                "USING GIN (tsv)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS chunks_tsv_boosted_gin ON chunks "
                "USING GIN (tsv_boosted)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS chunks_source_published_idx "
                "ON chunks (source, published_at DESC)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS chunks_full_body_sha_idx "
                "ON chunks (full_body_sha256)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS chunks_published_at_idx "
                "ON chunks (published_at DESC) "
                "WHERE superseded_by IS NULL"
            )
            # HNSW is the fast ANN index — cheap insert + fast
            # query. ``vector_cosine_ops`` matches the OpenAI embed
            # convention.
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_idx "
                "ON chunks USING hnsw (embedding vector_cosine_ops)"
            )
        self._initialized = True

    # ---- write path ----------------------------------------------------

    # Aliases so ``drain()`` can treat FlatGraph and KnowledgeGraph
    # interchangeably. The Graphiti path's contract is the older,
    # episode-named one; we adopt those names here as thin wrappers
    # over the chunk-native API.

    async def has_unchanged_episode(
        self, source: str, body_sha256: str
    ) -> bool:
        return await self.has_unchanged_chunk(source, body_sha256)

    async def upsert_episode(
        self,
        *,
        name: str,
        body: str,
        source: str,
        reference_time: datetime | None = None,
        group_id: str = "lighthouse",  # ignored — flat path has no
                                       # multi-tenant partitioning yet
    ) -> str:
        return await self.upsert_document(
            name=name,
            body=body,
            source=source,
            reference_time=reference_time,
        )

    async def has_unchanged_chunk(
        self, source: str, body_sha256: str
    ) -> bool:
        """Delta-skip pre-check — does an existing chunk for this
        source carry the same full-body hash? Same semantics as
        :meth:`KnowledgeGraph.has_unchanged_episode`."""
        pool = await self._pool_lazy()
        async with pool.acquire() as conn:
            return bool(
                await conn.fetchval(
                    "SELECT 1 FROM chunks "
                    "WHERE source = $1 AND full_body_sha256 = $2 LIMIT 1",
                    source,
                    body_sha256,
                )
            )

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
        """Splits the body into chunks, embeds each, upserts as
        ``chunks`` rows. Returns the uuid of the first chunk for
        caller bookkeeping."""
        if not body or not body.strip():
            return ""
        ref = reference_time or datetime.now(UTC)
        full_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        chunks = _split_episode_body(body, cap=MAX_CHUNK_CHARS)

        embeddings = await self._embed_batch(chunks)
        rows: list[tuple] = []
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings, strict=True)):
            chunk_name = (
                name
                if len(chunks) == 1
                else f"{name} (part {i + 1}/{len(chunks)})"
            )
            chunk_uuid = _deterministic_uuid(source, full_hash, i)
            rows.append(
                (
                    chunk_uuid,
                    chunk_name,
                    source,
                    url,
                    chunk,
                    hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
                    full_hash,
                    ref,
                    version,
                    i,
                    len(chunks),
                    _vector_literal(emb),
                )
            )

        pool = await self._pool_lazy()
        async with pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO chunks (
                    uuid, name, source, url, content,
                    content_sha256, full_body_sha256, published_at,
                    version, chunk_index, chunk_count, embedding
                ) VALUES (
                    $1, $2, $3, $4, $5,
                    $6, $7, $8, $9, $10, $11, $12::vector
                )
                ON CONFLICT (uuid) DO UPDATE SET
                    name = EXCLUDED.name,
                    source = EXCLUDED.source,
                    url = EXCLUDED.url,
                    content = EXCLUDED.content,
                    content_sha256 = EXCLUDED.content_sha256,
                    full_body_sha256 = EXCLUDED.full_body_sha256,
                    published_at = EXCLUDED.published_at,
                    version = EXCLUDED.version,
                    chunk_index = EXCLUDED.chunk_index,
                    chunk_count = EXCLUDED.chunk_count,
                    embedding = EXCLUDED.embedding,
                    ingested_at = now()
                """,
                rows,
            )
        return rows[0][0]

    # ---- read path -----------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        top_k: int = DEFAULT_TOP_K,
        after: datetime | None = None,
        before: datetime | None = None,
        version: str | None = None,
        include_release_notes: bool | None = None,
        include_superseded: bool = False,
        use_summary_boost: bool = True,
        use_reranker: bool = True,
    ) -> list[FlatHit]:
        """Hybrid BM25 + vector search with time-aware filters.

        ``after`` / ``before`` filter by ``published_at``. Pass
        ``after=<frontier_cutoff>`` to surface only post-cutoff
        material — the canonical "what frontier models don't have"
        slice.

        ``include_release_notes`` overrides the heuristic that
        excludes ``gh-releases-*``/``rss-*`` sources on how-to
        queries. ``include_superseded`` brings back chunks marked
        replaced by a newer version (default off).

        ``use_summary_boost`` (default True): BM25 against the
        boosted tsvector that weights summary + keywords + tags
        above raw content. Audit showed this lifts mean useful
        from 2.22 → 2.26 with no measurable downside.

        ``use_reranker`` (default True): after the BM25+vector RRF
        merge, re-rank the top top_k*3 candidates with gpt-4o-mini
        structured output. Audit showed this lifts mean useful from
        2.22 → 2.33 (parity with the Graphiti baseline) and produces
        the big per-domain wins (security 60→20%, devops 33→50%,
        mobile useful 2.88→3.04). Costs ~$0.002 per query and
        adds ~60 ms latency. Disable with use_reranker=False if you
        need raw hybrid output (e.g. for comparing engines).
        """
        if not query or not query.strip():
            return []
        if include_release_notes is None:
            include_release_notes = _query_wants_releases(query)
        excluded_prefixes: list[str] = (
            [] if include_release_notes else ["gh-releases-", "rss-"]
        )

        q_embedding = (await self._embed_batch([query]))[0]
        bm25 = await self._search_bm25(
            query,
            limit=top_k * BM25_OVERSAMPLE,
            after=after,
            before=before,
            version=version,
            excluded_prefixes=excluded_prefixes,
            include_superseded=include_superseded,
            use_summary_boost=use_summary_boost,
        )
        vec = await self._search_vector(
            q_embedding,
            limit=top_k * VECTOR_OVERSAMPLE,
            after=after,
            before=before,
            version=version,
            excluded_prefixes=excluded_prefixes,
            include_superseded=include_superseded,
        )

        # RRF fusion with BM25 weighted slightly higher (audit
        # evidence has BM25 as the stronger signal). k=60 is the
        # canonical RRF constant.
        scored: dict[str, tuple[float, FlatHit]] = {}
        for rank, h in enumerate(bm25):
            scored[h.node_id] = (1.0 / (60 + rank) * 1.2, h)
        for rank, h in enumerate(vec):
            prior = scored.get(h.node_id)
            if prior is None:
                scored[h.node_id] = (1.0 / (60 + rank), h)
            else:
                scored[h.node_id] = (prior[0] + 1.0 / (60 + rank), prior[1])

        merged = sorted(scored.values(), key=lambda kv: kv[0], reverse=True)
        out: list[FlatHit] = []
        seen_keys: set[str] = set()
        for score, hit in merged:
            key = " ".join((hit.summary or "").lower().split())[:80]
            if key in seen_keys:
                continue
            seen_keys.add(key)
            hit.score = score
            out.append(hit)
            # Pull 3× more candidates than top_k when reranking is on
            # — the reranker has more to sort over. Without rerank we
            # still stop at top_k to avoid wasted serialisation.
            cap = (top_k * 3) if use_reranker else top_k
            if len(out) >= cap:
                break

        if use_reranker and len(out) > top_k:
            try:
                out = await self._rerank(query, out, top_k=top_k)
            except Exception:
                logger.exception(
                    "reranker failed — returning hybrid order"
                )
                out = out[:top_k]
        else:
            out = out[:top_k]
        return out

    async def _rerank(
        self,
        query: str,
        candidates: list[FlatHit],
        *,
        top_k: int,
    ) -> list[FlatHit]:
        """Cross-encoder-style rerank via gpt-4o-mini structured output.

        Why an LLM and not Cohere/Jina/BGE: gpt-4o-mini is already on
        our key, costs ~$0.002 per query for top-30 candidates, and
        the structured-output mode keeps the response deterministic.
        When we want lower latency we can swap in a purpose-built
        cross-encoder (jina-reranker-v2, bge-reranker) by replacing
        only this method.
        """
        if not candidates or top_k <= 0:
            return candidates[:top_k]

        from openai import AsyncOpenAI

        s = self._settings
        if not s.openai_api_key:
            return candidates[:top_k]
        client = AsyncOpenAI(api_key=s.openai_api_key)

        # Trim each candidate to a short snippet — full content would
        # blow the prompt budget and the reranker doesn't need the
        # whole thing to judge relevance.
        snippets = []
        for i, hit in enumerate(candidates):
            text = (hit.summary or "")[:600]
            snippets.append(f"[{i}] {text}")
        prompt = (
            f"Query: {query!r}\n\n"
            f"Re-rank the following {len(candidates)} candidates by how "
            f"directly they answer the query. Return the {top_k} most "
            "relevant indices in order, most relevant first. "
            "Reply with JSON only.\n\n"
            "Candidates:\n" + "\n\n".join(snippets)
        )

        try:
            resp = await client.chat.completions.create(
                model=s.openai_small_model or "gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=400,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "ranking",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "ranking": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                }
                            },
                            "required": ["ranking"],
                        },
                    },
                },
            )
            import json

            text = (resp.choices[0].message.content or "").strip()
            data = json.loads(text)
            ranking = list(data.get("ranking", []))
        except Exception:
            logger.exception("rerank LLM call failed")
            return candidates[:top_k]

        # Map back to FlatHit objects in the new order. Drop indices
        # out of range; backfill with the original order if the LLM
        # under-returns.
        seen: set[int] = set()
        ranked: list[FlatHit] = []
        for idx in ranking:
            if not isinstance(idx, int) or idx < 0 or idx >= len(candidates):
                continue
            if idx in seen:
                continue
            seen.add(idx)
            # Bump score so downstream telemetry has a usable signal.
            candidates[idx].score = 1.0 / (1 + len(ranked))
            ranked.append(candidates[idx])
            if len(ranked) >= top_k:
                break
        for i, h in enumerate(candidates):
            if len(ranked) >= top_k:
                break
            if i in seen:
                continue
            ranked.append(h)
        return ranked[:top_k]

    async def fetch_source(self, chunk_id: str):
        """Return the chunk row as a ``GraphSource``-shaped object so
        the MCP server treats us identically to the Graphiti path.

        No truncation here — callers (MCP, bench) handle ``max_chars``.
        Returns ``None`` when the uuid doesn't match a Chunk row.
        Accepts a string (full uuid) or short-prefix (the wire form
        the bench uses, e.g. ``ep:9f3a2c``).
        """
        # Resolve short prefix → full uuid via column scan. Cheap
        # (PK lookup when full), bounded (LIMIT 1) when prefix.
        from lighthouse.core.graph import GraphSource

        pool = await self._pool_lazy()
        async with pool.acquire() as conn:
            row = None
            try:
                uid = _uuid.UUID(chunk_id)
                row = await conn.fetchrow(
                    "SELECT uuid, name, source, url, content, "
                    "published_at, ingested_at FROM chunks WHERE uuid = $1",
                    uid,
                )
            except (ValueError, AttributeError):
                # Short prefix — fall back to LIKE on the uuid string.
                row = await conn.fetchrow(
                    "SELECT uuid, name, source, url, content, "
                    "published_at, ingested_at FROM chunks "
                    "WHERE replace(uuid::text, '-', '') LIKE $1 LIMIT 1",
                    str(chunk_id).lower() + "%",
                )
        if row is None:
            return None
        return GraphSource(
            episode_id=str(row["uuid"]),
            name=row["name"] or "",
            source=row["source"] or "",
            content=row["content"] or "",
            created_at=row["ingested_at"],
            valid_at=row["published_at"],
        )

    async def fetch(self, node_id: str):
        """No entity layer in flat-RAG — return ``None``. Kept so the
        MCP server can call ``fetch_entity`` against either backend
        and get a graceful empty result on the flat side instead of
        an AttributeError."""
        return None

    # ---- internals -----------------------------------------------------

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Batch embedding via OpenAI. One request per chunk batch
        instead of per-chunk."""
        if not texts:
            return []
        from openai import AsyncOpenAI

        s = self._settings
        client = AsyncOpenAI(api_key=s.openai_api_key)
        resp = await client.embeddings.create(
            model=s.openai_embedding_model,
            input=texts,
            dimensions=int(s.openai_embedding_dim),
        )
        return [d.embedding for d in resp.data]

    async def _search_bm25(
        self,
        query: str,
        *,
        limit: int,
        after: datetime | None,
        before: datetime | None,
        version: str | None,
        excluded_prefixes: list[str],
        include_superseded: bool,
        use_summary_boost: bool = False,
    ) -> list[FlatHit]:
        # ``websearch_to_tsquery`` accepts loose human queries
        # (handles AND/OR/quoted phrases) — closer to BM25 ergonomics
        # than ``plainto_tsquery``.
        tsv_col = "tsv_boosted" if use_summary_boost else "tsv"
        clauses = [f"{tsv_col} @@ websearch_to_tsquery('english', $1)"]
        params: list[Any] = [query]
        if not include_superseded:
            clauses.append("superseded_by IS NULL")
        if after is not None:
            params.append(after)
            clauses.append(f"published_at >= ${len(params)}")
        if before is not None:
            params.append(before)
            clauses.append(f"published_at < ${len(params)}")
        if version is not None:
            params.append(version)
            clauses.append(f"version = ${len(params)}")
        if excluded_prefixes:
            params.append(excluded_prefixes)
            clauses.append(
                f"NOT (source = ANY (SELECT prefix || '%' FROM "
                f"unnest(${len(params)}::text[]) AS prefix))"
            )
            # The construct above doesn't work cleanly; simpler:
            # we replace with NOT (source LIKE ANY (...)).
            clauses[-1] = (
                f"NOT (source LIKE ANY (SELECT prefix || '%' FROM "
                f"unnest(${len(params)}::text[]) AS prefix))"
            )
        params.append(int(limit))
        cypher = (
            "SELECT uuid, name, source, url, content, published_at, "
            f"ts_rank_cd({tsv_col}, websearch_to_tsquery('english', $1)) AS score "
            "FROM chunks "
            "WHERE " + " AND ".join(clauses) + " "
            f"ORDER BY score DESC LIMIT ${len(params)}"
        )
        pool = await self._pool_lazy()
        async with pool.acquire() as conn:
            rows = await conn.fetch(cypher, *params)
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
        include_superseded: bool,
    ) -> list[FlatHit]:
        # Cosine distance: lower is closer. ``1 - distance`` → score.
        # pgvector accepts the literal as ``vector`` after cast.
        vec_lit = _vector_literal(embedding)
        clauses = ["TRUE"]
        params: list[Any] = []
        if not include_superseded:
            clauses.append("superseded_by IS NULL")
        if after is not None:
            params.append(after)
            clauses.append(f"published_at >= ${len(params)}")
        if before is not None:
            params.append(before)
            clauses.append(f"published_at < ${len(params)}")
        if version is not None:
            params.append(version)
            clauses.append(f"version = ${len(params)}")
        if excluded_prefixes:
            params.append(excluded_prefixes)
            clauses.append(
                f"NOT (source LIKE ANY (SELECT prefix || '%' FROM "
                f"unnest(${len(params)}::text[]) AS prefix))"
            )
        params.extend([vec_lit, int(limit)])
        cypher = (
            "SELECT uuid, name, source, url, content, published_at, "
            f"1 - (embedding <=> ${len(params) - 1}::vector) AS score "
            "FROM chunks "
            "WHERE " + " AND ".join(clauses) + " "
            f"ORDER BY embedding <=> ${len(params) - 1}::vector ASC "
            f"LIMIT ${len(params)}"
        )
        pool = await self._pool_lazy()
        async with pool.acquire() as conn:
            rows = await conn.fetch(cypher, *params)
        return [_row_to_hit(r) for r in rows]


def _row_to_hit(row: Any) -> FlatHit:
    content = (row["content"] or "").strip()
    snippet = content[:280] + ("…" if len(content) > 280 else "")
    name = row["name"] or ""
    summary = f"# {name}\n{snippet}" if name else snippet
    return FlatHit(
        node_id=str(row["uuid"]),
        summary=summary,
        source=str(row["source"] or ""),
        url=row["url"] or None,
        published_at=row["published_at"],
        score=float(row["score"]) if row["score"] is not None else 0.0,
        episode_ids=[str(row["uuid"])],
    )


def _vector_literal(emb: list[float]) -> str:
    """pgvector literal — asyncpg doesn't auto-encode lists into the
    vector type, so we pass the canonical ``[v1, v2, ...]`` text
    form and cast in SQL."""
    return "[" + ",".join(f"{v:.6f}" for v in emb) + "]"


def _deterministic_uuid(source: str, full_hash: str, chunk_index: int) -> str:
    """Stable uuid for re-ingest idempotency. Same body + same chunk
    index → same uuid → INSERT ... ON CONFLICT no-ops."""
    base = f"{source}:{full_hash}:{chunk_index}"
    return str(_uuid.UUID(hashlib.md5(base.encode("utf-8")).hexdigest()))


def _strip_neon_extras(url: str) -> str:
    """Drop asyncpg-incompatible query params from a Neon URL.

    Neon's pooler URL ships ``channel_binding=require`` which
    asyncpg doesn't recognise — silently strip it.
    """
    if "?" not in url:
        return url
    head, qs = url.split("?", 1)
    keep = []
    for kv in qs.split("&"):
        if not kv:
            continue
        k = kv.split("=", 1)[0]
        if k.lower() in {"channel_binding"}:
            continue
        keep.append(kv)
    return head + ("?" + "&".join(keep) if keep else "")


_RELEASE_KW_RE = re.compile(
    r"what'?s new|whats new|what is new|release note|changelog|"
    r"since version|added in v|deprecate"
)
_VERSION_TOKEN_RE = re.compile(r"\bv\d{1,2}(\.\d+)*\b", re.IGNORECASE)


def _query_wants_releases(query: str) -> bool:
    ql = query.lower()
    return bool(_RELEASE_KW_RE.search(ql) or _VERSION_TOKEN_RE.search(ql))
