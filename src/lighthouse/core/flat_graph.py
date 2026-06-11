"""Flat-RAG retrieval engine on Postgres + pgvector.

The single retrieval backend. Keeps only what audit-evidence shows is
actually load-bearing for retrieval:

- one ``chunks`` row per ingested document chunk
- ``tsvector`` GIN index over ``name || content`` (BM25-ish via
  ``ts_rank_cd``)
- ``vector(N)`` HNSW index over ``embedding`` (cosine)
- ``published_at`` + optional ``version`` + optional
  ``superseded_by`` for time-aware filtering

What this **doesn't** do (deliberate):
- No entity extraction. Each ingest pass embeds the body and
  indexes it; no LLM call to enumerate entities/relations.
- No cross-chunk dedup at extraction time. The chunk uuid is
  derived from ``workspace_id`` + ``source`` + content hash so
  re-running an ingest is idempotent and tenant-isolated.
- No "communities" / BFS / saga search recipes. Practical hybrid
  retrieval (BM25 + vector + RRF fusion) covers every query shape
  the bench actually uses.

Row-level multi-tenant: every read/write carries a mandatory
``workspace_id`` and the chunk uuid folds it in, so one Postgres can
serve many isolated workspaces (the reserved ``public`` workspace holds
the single-tenant reference corpus).
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
from lighthouse.core.migrator import run_migrations

logger = logging.getLogger(__name__)


# Per-chunk char cap (fits comfortably in any embedder's token
# budget). Re-exported for callers that want to size their connectors
# symmetrically.
MAX_CHUNK_CHARS = 12000

# Reserved workspace for the original single-tenant corpus (the public
# harborgang site). Backfilled by the 0002 column default; the read/write
# API treats a missing X-Workspace as this value so the public corpus
# keeps working unchanged.
PUBLIC_WORKSPACE = "public"

# Search-side knobs. Settled by spike-testing against the existing
# canonical queries; tuned more once we have an A/B audit run.
DEFAULT_TOP_K = 10
BM25_OVERSAMPLE = 3
VECTOR_OVERSAMPLE = 3


@dataclass(slots=True)
class FlatHit:
    """One search hit from the flat (pgvector) backend — a chunk row
    projected for the MCP / HTTP retrieval layers."""

    node_id: str
    summary: str
    source: str
    url: str | None = None
    published_at: datetime | None = None
    score: float = 0.0
    episode_ids: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SourceChunk:
    """The raw ingested chunk a search hit was extracted from.

    Returned by :meth:`FlatGraph.fetch_source` — agents read this when
    the short fact summary from search isn't enough and they want the
    surrounding text in one round-trip.
    """

    episode_id: str
    name: str
    source: str  # ``<connector>:<url>`` as recorded at ingest
    content: str
    created_at: datetime | None = None
    valid_at: datetime | None = None


def _split_body(body: str, *, cap: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split a long body into <=``cap``-char chunks at paragraph
    boundaries. Falls back to a hard split when no paragraph break is
    available inside the window — never returns an empty list.

    Paragraph-aware (not sentence-aware) so chunks preserve narrative
    units: RFC sections, OWASP/NIST paragraphs, blog posts are
    paragraph-separated, and sentence-level splits would fragment
    "X did Y because Z" across chunks.
    """
    if len(body) <= cap:
        return [body]
    out: list[str] = []
    remaining = body
    while len(remaining) > cap:
        # Last paragraph break inside the cap window.
        split_at = remaining.rfind("\n\n", 0, cap)
        if split_at < cap // 2:
            # No good paragraph break — try a sentence/word break.
            for sep in (". ", "\n", " "):
                idx = remaining.rfind(sep, cap // 2, cap)
                if idx > 0:
                    split_at = idx + len(sep)
                    break
            else:
                split_at = cap
        chunk = remaining[:split_at].strip()
        if chunk:
            out.append(chunk)
        remaining = remaining[split_at:].lstrip()
    if remaining.strip():
        out.append(remaining.strip())
    return out


class FlatGraph:
    """Flat-RAG retrieval facade — the engine the runner / CLI / API
    all use."""

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
                "retrieval engine."
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
        """Idempotent — applies any pending SQL migrations.

        Schema now lives in versioned files under ``core/migrations/``
        and is applied by :func:`run_migrations` (replaces the old
        in-code CREATE/ALTER block). The runner serializes concurrent
        boots with an advisory lock, so the previous per-index
        try/except race guard is no longer needed. The ``0001`` baseline
        is idempotent, so existing deployments migrate to the runner
        transparently (every statement no-ops, the version is recorded).
        """
        if self._initialized:
            return
        pool = await self._pool_lazy()
        async with pool.acquire() as conn:
            await run_migrations(
                conn,
                embedding_dim=int(self._settings.openai_embedding_dim),
            )
        self._initialized = True

    # ---- write path ----------------------------------------------------

    # ``*_episode`` aliases are the names ``drain()`` / proposals call;
    # thin wrappers over the chunk-native API below.

    async def has_unchanged_episode(
        self,
        source: str,
        body_sha256: str,
        recipe: str | None = None,
        *,
        workspace_id: str,
    ) -> bool:
        return await self.has_unchanged_chunk(
            source, body_sha256, recipe, workspace_id=workspace_id
        )

    async def upsert_episode(
        self,
        *,
        name: str,
        body: str,
        source: str,
        workspace_id: str,
        reference_time: datetime | None = None,
        recipe: str | None = None,
    ) -> str:
        return await self.upsert_document(
            name=name,
            body=body,
            source=source,
            workspace_id=workspace_id,
            reference_time=reference_time,
            recipe=recipe,
        )

    async def has_unchanged_chunk(
        self,
        source: str,
        body_sha256: str,
        recipe: str | None = None,
        *,
        workspace_id: str,
    ) -> bool:
        """Delta-skip pre-check.

        Two variants:
        * ``recipe=None`` — does the (source, hash) tuple already
          exist anywhere? Used by old single-recipe ingest paths.
        * ``recipe=<slug>`` — does it exist AND already carry this
          recipe membership? A miss means we still need to upsert
          (the row exists under another recipe and we need to
          merge the slug into ``recipes``).
        """
        pool = await self._pool_lazy()
        async with pool.acquire() as conn:
            if recipe is None:
                return bool(
                    await conn.fetchval(
                        "SELECT 1 FROM chunks "
                        "WHERE source = $1 AND full_body_sha256 = $2 "
                        "  AND workspace_id = $3 LIMIT 1",
                        source, body_sha256, workspace_id,
                    )
                )
            return bool(
                await conn.fetchval(
                    "SELECT 1 FROM chunks "
                    "WHERE source = $1 AND full_body_sha256 = $2 "
                    "  AND workspace_id = $3 AND $4 = ANY(recipes) LIMIT 1",
                    source, body_sha256, workspace_id, recipe,
                )
            )

    async def upsert_document(
        self,
        *,
        name: str,
        body: str,
        source: str,
        workspace_id: str,
        reference_time: datetime | None = None,
        url: str | None = None,
        version: str | None = None,
        recipe: str | None = None,
    ) -> str:
        """Splits the body into chunks, embeds each, upserts as
        ``chunks`` rows. Returns the uuid of the first chunk for
        caller bookkeeping.

        ``workspace_id`` scopes the row to one tenant. It's folded into
        the chunk uuid (see :func:`_deterministic_uuid`) so the same
        document ingested by two workspaces lands as two distinct rows
        instead of colliding on ON CONFLICT and leaking across tenants.

        ``recipe`` records the role-recipe slug that just ingested
        this source. On ON CONFLICT (existing uuid for the same
        canonical source) it's merged into the row's ``recipes``
        array, so a doc shared across recipes (RFC 9110 → network /
        performance / security) lives as a single row carrying
        multi-recipe membership instead of being duplicated.
        """
        if not body or not body.strip():
            return ""
        ref = reference_time or datetime.now(UTC)
        full_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        chunks = _split_body(body, cap=MAX_CHUNK_CHARS)
        recipes_arr = [recipe] if recipe else []

        if self._embeddings_enabled():
            embeddings: list[Any] = await self._embed_batch(chunks)
        else:
            # Keyword-only mode: chunks land with NULL embeddings and
            # are reachable via BM25; backfill by re-ingesting once an
            # OPENAI_API_KEY is configured.
            self._warn_keyword_only()
            embeddings = [None] * len(chunks)
        rows: list[tuple] = []
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings, strict=True)):
            chunk_name = (
                name
                if len(chunks) == 1
                else f"{name} (part {i + 1}/{len(chunks)})"
            )
            chunk_uuid = _deterministic_uuid(source, full_hash, i, workspace_id)
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
                    _vector_literal(emb) if emb is not None else None,
                    recipes_arr,
                    workspace_id,
                )
            )

        pool = await self._pool_lazy()
        async with pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO chunks (
                    uuid, name, source, url, content,
                    content_sha256, full_body_sha256, published_at,
                    version, chunk_index, chunk_count, embedding,
                    recipes, workspace_id
                ) VALUES (
                    $1, $2, $3, $4, $5,
                    $6, $7, $8, $9, $10, $11, $12::vector, $13, $14
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
                    recipes = (
                        SELECT ARRAY(SELECT DISTINCT unnest(
                            chunks.recipes || EXCLUDED.recipes))
                    ),
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
        workspace_id: str,
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
        2.22 → 2.33 and produces the big per-domain wins (security
        60→20%, devops 33→50%, mobile useful 2.88→3.04). Costs
        ~$0.002 per query and adds ~60 ms latency. Disable with
        use_reranker=False if you need raw hybrid output.
        """
        if not query or not query.strip():
            return []
        if include_release_notes is None:
            include_release_notes = _query_wants_releases(query)
        excluded_prefixes: list[str] = (
            [] if include_release_notes else ["gh-releases-", "rss-"]
        )

        bm25 = await self._search_bm25(
            query,
            workspace_id=workspace_id,
            limit=top_k * BM25_OVERSAMPLE,
            after=after,
            before=before,
            version=version,
            excluded_prefixes=excluded_prefixes,
            include_superseded=include_superseded,
            use_summary_boost=use_summary_boost,
        )
        vec: list[FlatHit] = []
        if self._embeddings_enabled():
            q_embedding = (await self._embed_batch([query]))[0]
            vec = await self._search_vector(
                q_embedding,
                workspace_id=workspace_id,
                limit=top_k * VECTOR_OVERSAMPLE,
                after=after,
                before=before,
                version=version,
                excluded_prefixes=excluded_prefixes,
                include_superseded=include_superseded,
            )
        else:
            # Keyword-only mode (no OPENAI_API_KEY): BM25 carries the
            # whole search; RRF fusion below degrades to BM25 ranking.
            self._warn_keyword_only()

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

    async def fetch_source(self, chunk_id: str, *, workspace_id: str):
        """Return the chunk row as a :class:`SourceChunk`.

        No truncation here — callers (MCP, bench) handle ``max_chars``.
        Returns ``None`` when the uuid doesn't match a Chunk row.
        Accepts a string (full uuid) or short-prefix (the wire form
        the bench uses, e.g. ``ep:9f3a2c``).
        """
        # Resolve short prefix → full uuid via column scan. Cheap
        # (PK lookup when full), bounded (LIMIT 1) when prefix.
        pool = await self._pool_lazy()
        async with pool.acquire() as conn:
            row = None
            try:
                uid = _uuid.UUID(chunk_id)
                row = await conn.fetchrow(
                    "SELECT uuid, name, source, url, content, "
                    "published_at, ingested_at FROM chunks "
                    "WHERE uuid = $1 AND workspace_id = $2",
                    uid, workspace_id,
                )
            except (ValueError, AttributeError):
                # Short prefix — fall back to LIKE on the uuid string.
                row = await conn.fetchrow(
                    "SELECT uuid, name, source, url, content, "
                    "published_at, ingested_at FROM chunks "
                    "WHERE replace(uuid::text, '-', '') LIKE $1 "
                    "  AND workspace_id = $2 LIMIT 1",
                    str(chunk_id).lower() + "%", workspace_id,
                )
        if row is None:
            return None
        return SourceChunk(
            episode_id=str(row["uuid"]),
            name=row["name"] or "",
            source=row["source"] or "",
            content=row["content"] or "",
            created_at=row["ingested_at"],
            valid_at=row["published_at"],
        )

    async def fetch(self, node_id: str, *, workspace_id: str = PUBLIC_WORKSPACE):
        """No entity layer in flat-RAG — always returns ``None``. Kept
        so the ``fetch_entity`` MCP tool / HTTP route get a graceful
        empty result instead of an AttributeError. ``workspace_id`` is
        accepted for call-site uniformity but unused."""
        del workspace_id
        return None

    # ---- internals -----------------------------------------------------

    def _embeddings_enabled(self) -> bool:
        return bool(self._settings.openai_api_key)

    def _warn_keyword_only(self) -> None:
        """One warning per process, not one per request."""
        if not getattr(self, "_keyword_only_warned", False):
            self._keyword_only_warned = True
            logger.warning(
                "OPENAI_API_KEY not set — keyword-only (BM25) mode: "
                "vector retrieval and embeddings are disabled"
            )

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Batch embedding via OpenAI. Two budgets to respect:

        * Per-input: ``text-embedding-3-*`` accept 8192 tokens. A
          12K-char chunk of dense markdown (code, math) can blow
          that. We pre-truncate to ~24K *characters* — well under
          8192 tokens even at the worst byte/token ratio.
        * Per-request: the API rejects requests over ~300K total
          tokens. We sub-batch into groups whose summed length stays
          well below that.

        Both caps got hit during the May 2026 role-recipe ingest;
        before this guard, a single oversized chunk would fail the
        whole document.
        """
        if not texts:
            return []
        from openai import AsyncOpenAI

        # Conservative: 4 chars/token is a typical floor for English
        # prose; code/JSON can dip to ~2. 24K chars at 2 chars/tok
        # = 12K tokens, still over 8192 — so cap at 16000 chars to
        # be safe for the most token-dense inputs.
        PER_INPUT_CHAR_CAP = 16_000
        # Sub-batch budget. ~64K chars ≈ 32K tokens for prose; even
        # at code density (~16K tokens) we stay an order of
        # magnitude under the 300K request cap.
        PER_BATCH_CHAR_BUDGET = 64_000

        truncated = [t[:PER_INPUT_CHAR_CAP] for t in texts]

        s = self._settings
        client = AsyncOpenAI(api_key=s.openai_api_key)

        out: list[list[float]] = []
        batch: list[str] = []
        batch_chars = 0
        for t in truncated:
            if batch and batch_chars + len(t) > PER_BATCH_CHAR_BUDGET:
                resp = await client.embeddings.create(
                    model=s.openai_embedding_model,
                    input=batch,
                    dimensions=int(s.openai_embedding_dim),
                )
                out.extend(d.embedding for d in resp.data)
                batch = []
                batch_chars = 0
            batch.append(t)
            batch_chars += len(t)
        if batch:
            resp = await client.embeddings.create(
                model=s.openai_embedding_model,
                input=batch,
                dimensions=int(s.openai_embedding_dim),
            )
            out.extend(d.embedding for d in resp.data)
        return out

    async def _search_bm25(
        self,
        query: str,
        *,
        workspace_id: str,
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
        params.append(workspace_id)
        clauses.append(f"workspace_id = ${len(params)}")
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
        workspace_id: str,
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
        params.append(workspace_id)
        clauses.append(f"workspace_id = ${len(params)}")
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


def _deterministic_uuid(
    source: str, full_hash: str, chunk_index: int, workspace_id: str
) -> str:
    """Stable uuid for re-ingest idempotency, scoped per workspace.

    Same workspace + body + chunk index → same uuid → INSERT ... ON
    CONFLICT no-ops (idempotent re-ingest). ``workspace_id`` MUST be in
    the hash: without it two tenants ingesting the same document derive
    the same uuid, so the second write's ON CONFLICT DO UPDATE would
    overwrite the first tenant's row — a cross-tenant leak and recipe
    bleed. With it, identical docs in different workspaces stay distinct
    rows."""
    base = f"{workspace_id}:{source}:{full_hash}:{chunk_index}"
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
