"""Row-level workspace isolation on the flat (pgvector) backend.

Gated like the other pg integration tests: set ``LIGHTHOUSE_TEST_PG_URL``
to a throwaway pgvector DB (e.g. ``docker run --rm -e POSTGRES_PASSWORD=pw
-e POSTGRES_DB=lh -p 55432:5432 pgvector/pgvector:pg16`` →
``LIGHTHOUSE_TEST_PG_URL=postgresql://postgres:pw@127.0.0.1:55432/lh``).

Embeddings are stubbed so the test needs no OpenAI key — tenant isolation
is enforced in SQL (``WHERE workspace_id = $`` + the workspace folded into
the chunk uuid), neither of which depends on the vector. Proves the three
guarantees K2 adds:

1. One tenant's write is invisible to another tenant's search.
2. ``has_unchanged_chunk`` (the delta-skip) is workspace-scoped.
3. The same document ingested by two tenants stays two distinct rows
   instead of colliding on ON CONFLICT.
"""

from __future__ import annotations

import hashlib
import os

import pytest

from lighthouse.core.config import Settings
from lighthouse.core.flat_graph import FlatGraph

_DSN = os.environ.get("LIGHTHOUSE_TEST_PG_URL")
_DIM = 8

pytestmark = pytest.mark.skipif(
    not _DSN, reason="set LIGHTHOUSE_TEST_PG_URL to run workspace isolation test"
)


def _graph() -> FlatGraph:
    settings = Settings(lighthouse_pg_url=_DSN, openai_embedding_dim=_DIM)
    g = FlatGraph(settings)

    # No OpenAI in CI. A constant non-zero vector keeps pgvector's cosine
    # HNSW index happy (zero vectors have undefined cosine distance).
    async def _fake_embed(texts: list[str]) -> list[list[float]]:
        return [[0.1] * _DIM for _ in texts]

    g._embed_batch = _fake_embed  # type: ignore[method-assign]
    return g


async def _bm25(g: FlatGraph, query: str, workspace_id: str):
    return await g._search_bm25(
        query,
        workspace_id=workspace_id,
        limit=10,
        after=None,
        before=None,
        version=None,
        excluded_prefixes=[],
        include_superseded=False,
    )


@pytest.mark.asyncio
async def test_workspace_isolation() -> None:
    g = _graph()
    pool = await g._pool_lazy()
    try:
        await g.initialize()
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM chunks WHERE workspace_id IN ('ws-a', 'ws-b')"
            )

        # Each tenant ingests its own distinct doc.
        await g.upsert_document(
            name="A doc", body="alpha aardvark unique",
            source="doc-a", workspace_id="ws-a", recipe="r",
        )
        await g.upsert_document(
            name="B doc", body="beta bumblebee unique",
            source="doc-b", workspace_id="ws-b", recipe="r",
        )

        # (1) A's content is visible to A, invisible to B.
        a_hits = await _bm25(g, "aardvark", "ws-a")
        b_hits = await _bm25(g, "aardvark", "ws-b")
        assert any(h.source == "doc-a" for h in a_hits)
        assert b_hits == []

        # (2) has_unchanged_chunk is workspace-scoped.
        a_hash = hashlib.sha256(b"alpha aardvark unique").hexdigest()
        assert await g.has_unchanged_chunk("doc-a", a_hash, workspace_id="ws-a")
        assert not await g.has_unchanged_chunk("doc-a", a_hash, workspace_id="ws-b")

        # (3) The same document ingested by both tenants stays two rows.
        for ws in ("ws-a", "ws-b"):
            await g.upsert_document(
                name="Shared", body="gamma gopher shared body",
                source="doc-shared", workspace_id=ws, recipe="r",
            )
        async with pool.acquire() as conn:
            n_rows = await conn.fetchval(
                "SELECT COUNT(*) FROM chunks WHERE source = 'doc-shared'"
            )
            n_ws = await conn.fetchval(
                "SELECT COUNT(DISTINCT workspace_id) FROM chunks "
                "WHERE source = 'doc-shared'"
            )
        assert n_rows == 2
        assert n_ws == 2
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM chunks WHERE workspace_id IN ('ws-a', 'ws-b')"
            )
        await g.close()
