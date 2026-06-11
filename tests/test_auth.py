"""Auth hardening: API-key retrieval auth, admin-token defaults,
webhook workspace isolation, rate limiting.

Postgres is faked at the asyncpg-pool seam (same convention as
tests/test_analytics.py).
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from lighthouse.api.dependencies import (
    _rate_limiter,
    get_graph,
    get_pg_pool,
    get_query_logger,
)
from lighthouse.api.main import create_app
from lighthouse.core.auth import KEY_PREFIX, generate_key, hash_key
from lighthouse.core.ratelimit import SlidingWindowLimiter

NOW = datetime.now(UTC)


class FakeConn:
    """Routes fetchrow/fetch/execute to canned responses by SQL substring."""

    def __init__(self, responses: list[tuple[str, object]] | None = None) -> None:
        self._responses = responses or []
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    def _match(self, query: str) -> object:
        for needle, value in self._responses:
            if needle in query:
                return value
        return None

    async def fetchrow(self, query: str, *args: object) -> object:
        self.executed.append((query, args))
        return self._match(query)

    async def fetch(self, query: str, *args: object) -> object:
        self.executed.append((query, args))
        return self._match(query) or []

    async def execute(self, query: str, *args: object) -> str:
        self.executed.append((query, args))
        matched = self._match(query)
        return matched if isinstance(matched, str) else "OK 1"

    async def fetchval(self, query: str, *args: object) -> object:
        self.executed.append((query, args))
        return self._match(query)

    async def executemany(self, query: str, rows: object) -> None:
        self.executed.append((query, (rows,)))


class FakePool:
    def __init__(self, responses: list[tuple[str, object]] | None = None) -> None:
        self.conn = FakeConn(responses)

    def acquire(self) -> FakePool:
        return self

    async def __aenter__(self) -> FakeConn:
        return self.conn

    async def __aexit__(self, *exc: object) -> None:
        return None


class NullLogger:
    def log(self, **kwargs: object) -> None:
        pass


def make_client(fake_graph, pool) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_graph] = lambda: fake_graph
    app.dependency_overrides[get_query_logger] = lambda: NullLogger()
    app.dependency_overrides[get_pg_pool] = lambda: pool
    return TestClient(app)


def key_row(workspace: str = "acme") -> dict[str, object]:
    return {
        "id": uuid4(),
        "workspace_id": workspace,
        "name": "test-key",
        "scopes": ["read"],
    }


# ───────────────────────── retrieval auth ─────────────────────────


def test_valid_key_binds_workspace(fake_graph) -> None:
    """Workspace comes from the key, not from the client header."""
    pool = FakePool([("UPDATE api_keys", key_row("acme"))])
    with make_client(fake_graph, pool) as client:
        res = client.get(
            "/v1/search",
            params={"q": "x"},
            headers={"Authorization": f"Bearer {KEY_PREFIX}abc"},
        )
    assert res.status_code == 200
    assert fake_graph.last_search_workspace == "acme"


def test_key_workspace_header_mismatch_403(fake_graph) -> None:
    pool = FakePool([("UPDATE api_keys", key_row("acme"))])
    with make_client(fake_graph, pool) as client:
        res = client.get(
            "/v1/search",
            params={"q": "x"},
            headers={
                "Authorization": f"Bearer {KEY_PREFIX}abc",
                "X-Workspace": "other-corp",
            },
        )
    assert res.status_code == 403


def test_unknown_or_revoked_key_401(fake_graph) -> None:
    pool = FakePool([("UPDATE api_keys", None)])
    with make_client(fake_graph, pool) as client:
        res = client.get(
            "/v1/search",
            params={"q": "x"},
            headers={"Authorization": f"Bearer {KEY_PREFIX}dead"},
        )
    assert res.status_code == 401


def test_auth_required_blocks_keyless(fake_graph, monkeypatch) -> None:
    monkeypatch.setenv("LIGHTHOUSE_RETRIEVAL_AUTH_REQUIRED", "true")
    with make_client(fake_graph, FakePool()) as client:
        assert client.get("/v1/search", params={"q": "x"}).status_code == 401
        # Header alone no longer selects a workspace.
        res = client.get(
            "/v1/search", params={"q": "x"}, headers={"X-Workspace": "acme"}
        )
        assert res.status_code == 401


def test_legacy_keyless_mode_still_works(fake_graph) -> None:
    """Default config: no key → header-or-public, unchanged behaviour."""
    with make_client(fake_graph, FakePool()) as client:
        res = client.get(
            "/v1/search", params={"q": "x"}, headers={"X-Workspace": "acme"}
        )
        assert res.status_code == 200
        assert fake_graph.last_search_workspace == "acme"


# ───────────────────────── admin guard ─────────────────────────


def test_admin_locked_by_default(fake_graph, monkeypatch) -> None:
    monkeypatch.delenv("LIGHTHOUSE_INSECURE_ADMIN", raising=False)
    with make_client(fake_graph, FakePool()) as client:
        res = client.get("/v1/importers/")
        assert res.status_code == 401
        assert "LIGHTHOUSE_ADMIN_TOKEN" in res.json()["detail"]
        assert client.get("/v1/webhooks/").status_code == 401
        assert client.get("/v1/corpus/stats").status_code == 401
        assert client.get("/v1/keys/").status_code == 401


def test_admin_token_grants_access(fake_graph, monkeypatch) -> None:
    monkeypatch.delenv("LIGHTHOUSE_INSECURE_ADMIN", raising=False)
    monkeypatch.setenv("LIGHTHOUSE_ADMIN_TOKEN", "sekrit")
    pool = FakePool([("FROM api_keys", [])])
    with make_client(fake_graph, pool) as client:
        assert client.get("/v1/keys/").status_code == 401
        res = client.get(
            "/v1/keys/", headers={"Authorization": "Bearer sekrit"}
        )
        assert res.status_code == 200


# ───────────────────────── key management ─────────────────────────


def test_create_key_returns_secret_once(fake_graph) -> None:
    created = key_row("acme")
    created.update({"created_at": NOW, "last_used_at": None, "revoked_at": None})
    pool = FakePool([("INSERT INTO api_keys", created)])
    with make_client(fake_graph, pool) as client:
        res = client.post(
            "/v1/keys/", json={"name": "ci"}, headers={"X-Workspace": "acme"}
        )
    assert res.status_code == 201
    body = res.json()
    assert body["secret"].startswith(KEY_PREFIX)
    # Only the hash reaches the database.
    _, args = pool.conn.executed[0]
    assert args[2] == hash_key(body["secret"])
    assert body["secret"] not in args


def test_generate_key_roundtrip() -> None:
    secret, digest = generate_key()
    assert secret.startswith(KEY_PREFIX)
    assert hash_key(secret) == digest
    assert len(digest) == 64


# ───────────────────────── webhooks isolation ─────────────────────────


async def test_emit_event_scopes_to_workspace() -> None:
    from lighthouse.webhooks.dispatcher import emit_event

    pool = FakePool([("SELECT id FROM webhooks", [{"id": uuid4()}])])
    n = await emit_event(pool, "importer.run.started", {"x": 1}, workspace_id="acme")
    assert n == 1
    select_q, select_args = pool.conn.executed[0]
    assert "workspace_id = $2" in select_q
    assert select_args[1] == "acme"
    insert_q, insert_args = pool.conn.executed[1]
    assert "workspace_id" in insert_q
    assert insert_args[-1] == "acme"


def test_webhook_list_scoped(fake_graph) -> None:
    pool = FakePool([("FROM webhooks", [])])
    with make_client(fake_graph, pool) as client:
        res = client.get("/v1/webhooks/", headers={"X-Workspace": "acme"})
    assert res.status_code == 200
    q, args = pool.conn.executed[0]
    assert "workspace_id = $1" in q
    assert args[0] == "acme"


# ───────────────────────── rate limiting ─────────────────────────


def test_sliding_window_limiter() -> None:
    lim = SlidingWindowLimiter()
    assert all(lim.allow("k", 3) for _ in range(3))
    assert not lim.allow("k", 3)
    assert lim.allow("other", 3)  # independent key
    assert lim.allow("k", 0)  # 0 disables


def test_search_rate_limited(fake_graph, monkeypatch) -> None:
    monkeypatch.setenv("LIGHTHOUSE_SEARCH_RATE_LIMIT_PER_MINUTE", "2")
    # Settings are lru_cached per process — clear so the env applies.
    from lighthouse.core.config import get_settings

    get_settings.cache_clear()
    _rate_limiter.cache_clear()
    try:
        with make_client(fake_graph, FakePool()) as client:
            codes = [
                client.get("/v1/search", params={"q": "x"}).status_code
                for _ in range(3)
            ]
        assert codes == [200, 200, 429]
    finally:
        get_settings.cache_clear()
        _rate_limiter.cache_clear()


# ───────────────────────── keyword-only mode ─────────────────────────


async def test_search_without_openai_key_skips_vector(monkeypatch) -> None:
    """FlatGraph.search must not call the embeddings API when no
    OPENAI_API_KEY is configured — BM25 carries the search."""
    from lighthouse.core.flat_graph import FlatGraph

    g = FlatGraph.__new__(FlatGraph)

    class S:
        openai_api_key = ""

    g._settings = S()

    called: dict[str, bool] = {"embed": False, "vector": False}

    async def fake_bm25(*a, **kw):
        return []

    async def fake_embed(texts):
        called["embed"] = True
        return [[0.0]]

    async def fake_vector(*a, **kw):
        called["vector"] = True
        return []

    async def fake_rerank(query, candidates, top_k):
        return candidates[:top_k]

    monkeypatch.setattr(g, "_search_bm25", fake_bm25, raising=False)
    monkeypatch.setattr(g, "_embed_batch", fake_embed, raising=False)
    monkeypatch.setattr(g, "_search_vector", fake_vector, raising=False)

    hits = await FlatGraph.search(g, "query", workspace_id="public", use_reranker=False)
    assert hits == []
    assert called == {"embed": False, "vector": False}


async def test_ingest_without_openai_key_stores_null_embeddings(monkeypatch) -> None:
    """upsert_document in keyword-only mode must not call the embeddings
    API and must insert NULL embeddings (BM25 still reaches the rows)."""
    from lighthouse.core.flat_graph import FlatGraph

    g = FlatGraph.__new__(FlatGraph)

    class S:
        openai_api_key = ""

    g._settings = S()
    pool = FakePool()

    async def fake_pool_lazy():
        return pool

    async def fail_embed(texts):
        raise AssertionError("embeddings API must not be called")

    monkeypatch.setattr(g, "_pool_lazy", fake_pool_lazy, raising=False)
    monkeypatch.setattr(g, "_embed_batch", fail_embed, raising=False)

    chunk_uuid = await FlatGraph.upsert_document(
        g, name="doc", body="hello world", source="src", workspace_id="acme"
    )
    assert chunk_uuid
    insert_q, (rows,) = pool.conn.executed[0]
    assert "INSERT INTO chunks" in insert_q
    assert rows[0][11] is None  # embedding column
    assert rows[0][13] == "acme"  # workspace column


# ───────────────────────── key management routes ─────────────────────────


def test_keys_list_scoped(fake_graph) -> None:
    pool = FakePool([("FROM api_keys", [])])
    with make_client(fake_graph, pool) as client:
        res = client.get("/v1/keys/", headers={"X-Workspace": "acme"})
    assert res.status_code == 200
    q, args = pool.conn.executed[0]
    assert "workspace_id = $1" in q
    assert args[0] == "acme"


def test_revoke_key_scoped(fake_graph) -> None:
    revoked = key_row("acme")
    revoked.update({"created_at": NOW, "last_used_at": None, "revoked_at": NOW})
    pool = FakePool([("revoked_at = NOW()", revoked)])
    with make_client(fake_graph, pool) as client:
        res = client.delete(
            f"/v1/keys/{revoked['id']}", headers={"X-Workspace": "acme"}
        )
    assert res.status_code == 200
    assert res.json()["revoked_at"] is not None
    q, args = pool.conn.executed[0]
    assert "workspace_id = $2" in q
    assert args[1] == "acme"


def test_revoke_unknown_key_404(fake_graph) -> None:
    with make_client(fake_graph, FakePool()) as client:
        res = client.delete(f"/v1/keys/{uuid4()}")
    assert res.status_code == 404


def test_create_key_rejects_empty_name(fake_graph) -> None:
    with make_client(fake_graph, FakePool()) as client:
        assert client.post("/v1/keys/", json={"name": ""}).status_code == 422


# ───────────────────────── webhook route scoping ─────────────────────────


def webhook_row() -> dict[str, object]:
    return {
        "id": uuid4(),
        "url": "https://example.com/hook",
        "events": ["*"],
        "enabled": True,
        "description": None,
        "created_at": NOW,
        "last_delivery_at": None,
        "last_status": None,
        "last_error": None,
    }


def test_webhook_create_stamps_workspace(fake_graph) -> None:
    pool = FakePool([("INSERT INTO webhooks", webhook_row())])
    with make_client(fake_graph, pool) as client:
        res = client.post(
            "/v1/webhooks/",
            json={"url": "https://example.com/hook"},
            headers={"X-Workspace": "acme"},
        )
    assert res.status_code == 201
    assert res.json()["secret"]  # echoed once
    q, args = pool.conn.executed[0]
    assert "workspace_id" in q
    assert args[-1] == "acme"


def test_webhook_get_cross_workspace_404(fake_graph) -> None:
    # fetchrow finds nothing for the caller's workspace → 404, even if
    # the row exists under another tenant.
    with make_client(fake_graph, FakePool()) as client:
        res = client.get(
            f"/v1/webhooks/{uuid4()}", headers={"X-Workspace": "intruder"}
        )
    assert res.status_code == 404


def test_webhook_delete_scoped(fake_graph) -> None:
    pool = FakePool([("DELETE FROM webhooks", "DELETE 0")])
    with make_client(fake_graph, pool) as client:
        res = client.delete(
            f"/v1/webhooks/{uuid4()}", headers={"X-Workspace": "intruder"}
        )
    assert res.status_code == 404
    q, args = pool.conn.executed[0]
    assert "workspace_id = $2" in q
    assert args[1] == "intruder"


def test_webhook_test_event_scoped(fake_graph) -> None:
    wh_id = uuid4()
    pool = FakePool([
        ("SELECT id FROM webhooks WHERE id", {"id": wh_id}),
        ("INSERT INTO webhook_deliveries", {"id": uuid4()}),
    ])
    with make_client(fake_graph, pool) as client:
        res = client.post(
            f"/v1/webhooks/{wh_id}/test", headers={"X-Workspace": "acme"}
        )
    assert res.status_code == 202
    select_q, select_args = pool.conn.executed[0]
    assert "workspace_id = $2" in select_q and select_args[1] == "acme"
    insert_q, insert_args = pool.conn.executed[1]
    assert "workspace_id" in insert_q and insert_args[-1] == "acme"


def test_webhook_deliveries_scoped(fake_graph) -> None:
    pool = FakePool([("FROM webhook_deliveries", [])])
    with make_client(fake_graph, pool) as client:
        res = client.get(
            f"/v1/webhooks/{uuid4()}/deliveries", headers={"X-Workspace": "acme"}
        )
    assert res.status_code == 200
    q, args = pool.conn.executed[0]
    assert "JOIN webhooks" in q and "workspace_id = $2" in q
    assert args[1] == "acme"


def test_webhook_redeliver_scoped(fake_graph) -> None:
    pool = FakePool([("UPDATE webhook_deliveries", "UPDATE 0")])
    with make_client(fake_graph, pool) as client:
        res = client.post(
            f"/v1/webhooks/{uuid4()}/deliveries/{uuid4()}/redeliver",
            headers={"X-Workspace": "intruder"},
        )
    assert res.status_code == 404
    q, args = pool.conn.executed[0]
    assert "workspace_id = $3" in q
    assert args[2] == "intruder"


# ───────────────────────── corpus scoping ─────────────────────────


def test_corpus_stats_scoped(fake_graph) -> None:
    pool = FakePool([
        ("COUNT(DISTINCT r)", 1),
        ("MAX(ingested_at)", {
            "total_chunks": 5, "total_sources": 2,
            "chunks_with_summary": 5, "chunks_with_embedding": 5,
            "last_ingest_at": NOW,
        }),
    ])
    with make_client(fake_graph, pool) as client:
        res = client.get("/v1/corpus/stats", headers={"X-Workspace": "acme"})
    assert res.status_code == 200
    assert res.json()["total_chunks"] == 5
    q, args = pool.conn.executed[0]
    assert "workspace_id = $1" in q
    assert args[0] == "acme"


# ───────────────────────── MCP workspace auth ─────────────────────────


def _mcp_ctx(headers: dict[str, str] | None):
    from types import SimpleNamespace

    if headers is None:
        # stdio transport: accessing .request raises
        class NoRequest:
            @property
            def request(self):
                raise RuntimeError("no http request")

        return SimpleNamespace(request_context=NoRequest())
    return SimpleNamespace(
        request_context=SimpleNamespace(
            request=SimpleNamespace(headers=headers)
        )
    )


async def test_mcp_stdio_falls_back_to_public() -> None:
    from lighthouse.mcp.server import _workspace_from_ctx

    assert await _workspace_from_ctx(_mcp_ctx(None)) == "public"


async def test_mcp_key_binds_workspace(monkeypatch) -> None:
    from lighthouse.mcp.server import _workspace_from_ctx

    pool = FakePool([("UPDATE api_keys", key_row("acme"))])

    async def fake_pool():
        return pool

    monkeypatch.setattr("lighthouse.api.dependencies.get_pg_pool", fake_pool)
    ws = await _workspace_from_ctx(
        _mcp_ctx({"authorization": f"Bearer {KEY_PREFIX}abc"})
    )
    assert ws == "acme"


async def test_mcp_auth_required_rejects_keyless(monkeypatch) -> None:
    from fastapi import HTTPException

    from lighthouse.mcp.server import _workspace_from_ctx

    monkeypatch.setenv("LIGHTHOUSE_RETRIEVAL_AUTH_REQUIRED", "true")
    try:
        await _workspace_from_ctx(_mcp_ctx({"x-workspace": "acme"}))
    except HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("expected 401")


# ───────────────────────── fetch_source auth ─────────────────────────


def test_fetch_source_key_mismatch_403(fake_graph) -> None:
    pool = FakePool([("UPDATE api_keys", key_row("acme"))])
    with make_client(fake_graph, pool) as client:
        res = client.get(
            "/v1/fetch_source/some-id",
            headers={
                "Authorization": f"Bearer {KEY_PREFIX}abc",
                "X-Workspace": "other",
            },
        )
    assert res.status_code == 403
