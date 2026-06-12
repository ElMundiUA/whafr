"""Integration scenario tests — real user journeys on a real pgvector DB.

Gated like the other integration tests: skipped unless a Postgres DSN is
provided via ``LIGHTHOUSE_TEST_PG_URL`` (a throwaway pgvector DB, e.g.
``docker run --rm -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=lh -p 55432:5432
pgvector/pgvector:pg16`` →
``LIGHTHOUSE_TEST_PG_URL=postgresql://postgres:pw@127.0.0.1:55432/lh``).

Unlike the unit suite (FakeGraph + recording fakes) these run the real
migrations, the real FlatGraph SQL, the real auth/webhook/analytics
queries — with ZERO LLM keys (BM25-only ingest + search). Journeys:

- S1  solo dev, zero keys: fresh DB → migrate → ingest markdown →
      BM25 search finds the right doc, embeddings stay NULL.
- S3  team API-key lifecycle: generate → lookup (stamps last_used_at)
      → revoke → lookup misses; hash uniqueness across workspaces.
- S5  org multi-workspace isolation: corpus, webhook deliveries and
      analytics aggregates never leak across workspaces; gap triage
      PATCH upserts (ON CONFLICT path).
- S6  brownfield upgrade: data written on a 0005-era schema survives
      re-running the migrator; webhooks land in 'public'.
- HTTP smoke: POST /v1/keys → Bearer lh_ search scoped by the key →
      mismatched X-Workspace 403 → analytics reflect only that tenant.

Each test cleans up its own rows (``scenario-*`` sources / workspaces)
so reruns against the same DB pass.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator
from pathlib import Path

import asyncpg
import pytest
from fastapi.testclient import TestClient

from lighthouse.core.auth import generate_key, lookup_key
from lighthouse.core.config import Settings, get_settings
from lighthouse.core.flat_graph import FlatGraph
from lighthouse.core.migrator import run_migrations
from lighthouse.core.query_log import QueryLogger
from lighthouse.webhooks.dispatcher import emit_event

_DSN = os.environ.get("LIGHTHOUSE_TEST_PG_URL")
_DIM = 8

pytestmark = pytest.mark.skipif(
    not _DSN, reason="set LIGHTHOUSE_TEST_PG_URL to run scenario integration tests"
)

_ALL_MIGRATIONS = [
    "0001_baseline.sql",
    "0002_workspace_id.sql",
    "0003_importers.sql",
    "0004_importer_workspace_name_unique.sql",
    "0005_webhooks.sql",
    "0006_query_log.sql",
    "0007_query_log_scores.sql",
    "0008_api_keys.sql",
    "0009_webhooks_workspace.sql",
]

_ALL_TABLES = [
    "webhook_deliveries",
    "webhooks",
    "importer_runs",
    "importers",
    "query_log",
    "coverage_gap_status",
    "api_keys",
    "chunks",
    "schema_migrations",
]


def _graph() -> FlatGraph:
    """Real FlatGraph against the test DSN with NO OpenAI key —
    keyword-only (BM25) mode, embeddings land NULL."""
    return FlatGraph(
        Settings(
            lighthouse_pg_url=_DSN,
            openai_embedding_dim=_DIM,
            openai_api_key="",
        )
    )


def _clear_dependency_caches() -> None:
    """The API layer is built on lru_cached singletons; clear them so a
    TestClient app re-reads the (monkeypatched) environment."""
    import lighthouse.api.dependencies as deps

    get_settings.cache_clear()
    deps.get_graph.cache_clear()
    deps.get_query_logger.cache_clear()
    deps.get_proposal_store.cache_clear()
    deps.get_librarian.cache_clear()
    deps.get_proposal_queue.cache_clear()
    deps._rate_limiter.cache_clear()


@contextlib.contextmanager
def _app_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[TestClient]:
    """Full-HTTP app over the REAL database: env points LIGHTHOUSE_PG_URL
    at the test DSN, admin is insecure-open, all LLM keys are empty."""
    import lighthouse.api.dependencies as deps

    monkeypatch.setenv("LIGHTHOUSE_PG_URL", _DSN or "")
    monkeypatch.setenv("LIGHTHOUSE_INSECURE_ADMIN", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("LIGHTHOUSE_PROPOSALS_DIR", str(tmp_path / "proposals"))
    monkeypatch.delenv("LIGHTHOUSE_ADMIN_TOKEN", raising=False)
    monkeypatch.delenv("LIGHTHOUSE_RETRIEVAL_AUTH_REQUIRED", raising=False)
    _clear_dependency_caches()
    # The app must build its asyncpg pool inside ITS event loop (the
    # TestClient portal thread) — a pool created in the pytest loop
    # would be unusable there. The lifespan creates it lazily and
    # close_pg_pool() resets the global on shutdown.
    assert deps._PG_POOL is None, "leaked asyncpg pool from a previous test"
    from lighthouse.api.main import create_app

    try:
        with TestClient(create_app()) as client:
            yield client
    finally:
        _clear_dependency_caches()


def _poll_overview(
    client: TestClient, workspace: str, want_questions: int, timeout: float = 8.0
) -> dict:
    """Query-log inserts are fire-and-forget asyncio tasks — poll the
    analytics overview until the expected rows landed (or time out and
    return whatever we last saw so the assert message is useful)."""
    deadline = time.monotonic() + timeout
    ov: dict = {}
    while time.monotonic() < deadline:
        r = client.get(
            "/v1/analytics/overview", headers={"X-Workspace": workspace}
        )
        assert r.status_code == 200, r.text
        ov = r.json()
        if ov.get("total_questions", 0) >= want_questions:
            return ov
        time.sleep(0.2)
    return ov


# ──────────────────────────────────────────────────────────────────
# S1 — solo dev, zero keys: fresh DB → migrate → ingest → BM25 search
# ──────────────────────────────────────────────────────────────────


async def test_s1_solo_dev_zero_keys() -> None:
    conn = await asyncpg.connect(_DSN)
    g = _graph()
    try:
        # Fresh database — drop everything the migrator owns.
        for table in _ALL_TABLES:
            await conn.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

        applied = await run_migrations(conn, embedding_dim=_DIM)
        assert applied == _ALL_MIGRATIONS
        # Idempotent re-apply: once via the runner, once via the engine.
        assert await run_migrations(conn, embedding_dim=_DIM) == []
        await g.initialize()

        # Real ingest of markdown-ish docs with NO OpenAI key.
        docs = [
            (
                "Kubernetes readiness probes",
                "# Kubernetes readiness probes\n\n"
                "A kubernetes readiness probe tells the kubelet when a "
                "container can accept traffic. Unlike liveness probes, a "
                "failing readiness probe removes the pod from Service "
                "endpoints without restarting it.",
                "scenario-s1-k8s",
            ),
            (
                "Magic-link authentication",
                "# Magic-link authentication\n\n"
                "Passwordless login: email the user a single-use signed "
                "token URL with a short expiry. Verify the token "
                "server-side and establish the session.",
                "scenario-s1-auth",
            ),
            (
                "Gherkin acceptance criteria",
                "# Gherkin acceptance criteria\n\n"
                "Given-When-Then scenarios make acceptance criteria "
                "executable. Keep one behaviour per scenario and avoid "
                "incidental detail.",
                "scenario-s1-gherkin",
            ),
        ]
        for name, body, source in docs:
            uid = await g.upsert_document(
                name=name, body=body, source=source, workspace_id="public"
            )
            assert uid

        # BM25 finds the right doc for a keyword query.
        hits = await g.search(
            "kubernetes readiness probe",
            workspace_id="public",
            top_k=5,
            use_reranker=False,
        )
        assert hits, "BM25 search returned nothing for an indexed keyword"
        assert hits[0].source == "scenario-s1-k8s"
        assert "readiness" in hits[0].summary.lower()
        # The other docs don't contain 'kubernetes' — they must not rank.
        assert {h.source for h in hits} == {"scenario-s1-k8s"}

        # A nonsense query returns [].
        assert (
            await g.search(
                "zorblefrazz wibblequx snargleblat",
                workspace_id="public",
                use_reranker=False,
            )
            == []
        )

        # Keyword-only mode: every ingested row has a NULL embedding.
        rows = await conn.fetch(
            "SELECT embedding FROM chunks WHERE source LIKE 'scenario-s1-%'"
        )
        assert len(rows) == len(docs)
        assert all(r["embedding"] is None for r in rows)
    finally:
        await conn.execute(
            "DELETE FROM chunks WHERE source LIKE 'scenario-s1-%'"
        )
        await g.close()
        await conn.close()


# ──────────────────────────────────────────────────────────────────
# S3 — team API-key lifecycle in real SQL
# ──────────────────────────────────────────────────────────────────


async def test_s3_team_api_keys_lifecycle() -> None:
    ws_a, ws_b = "scenario-s3-team-a", "scenario-s3-team-b"
    conn = await asyncpg.connect(_DSN)
    try:
        await run_migrations(conn, embedding_dim=_DIM)
        await conn.execute(
            "DELETE FROM api_keys WHERE workspace_id LIKE 'scenario-s3-%'"
        )

        secret_a, hash_a = generate_key()
        assert secret_a.startswith("lh_") and secret_a != hash_a
        key_a_id = await conn.fetchval(
            "INSERT INTO api_keys (workspace_id, name, key_hash) "
            "VALUES ($1, $2, $3) RETURNING id",
            ws_a,
            "ci key a",
            hash_a,
        )

        # Lookup resolves the secret and stamps last_used_at.
        assert (
            await conn.fetchval(
                "SELECT last_used_at FROM api_keys WHERE id = $1", key_a_id
            )
            is None
        )
        key = await lookup_key(conn, secret_a)
        assert key is not None
        assert key.workspace_id == ws_a
        assert key.id == key_a_id
        assert (
            await conn.fetchval(
                "SELECT last_used_at FROM api_keys WHERE id = $1", key_a_id
            )
            is not None
        )

        # A second workspace's key can't be confused with the first.
        secret_b, hash_b = generate_key()
        assert hash_b != hash_a
        await conn.execute(
            "INSERT INTO api_keys (workspace_id, name, key_hash) "
            "VALUES ($1, $2, $3)",
            ws_b,
            "ci key b",
            hash_b,
        )
        key_b = await lookup_key(conn, secret_b)
        assert key_b is not None and key_b.workspace_id == ws_b
        key_a_again = await lookup_key(conn, secret_a)
        assert key_a_again is not None and key_a_again.workspace_id == ws_a

        # key_hash is UNIQUE — the same secret can't be re-registered
        # under another workspace (which would hijack lookups).
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO api_keys (workspace_id, name, key_hash) "
                "VALUES ($1, $2, $3)",
                ws_b,
                "evil duplicate",
                hash_a,
            )

        # Revoke (soft) → lookup returns None; the other key still works.
        await conn.execute(
            "UPDATE api_keys SET revoked_at = NOW() WHERE id = $1", key_a_id
        )
        assert await lookup_key(conn, secret_a) is None
        assert await lookup_key(conn, secret_b) is not None
        # Unknown secrets never resolve.
        assert await lookup_key(conn, "lh_" + "0" * 48) is None
    finally:
        await conn.execute(
            "DELETE FROM api_keys WHERE workspace_id LIKE 'scenario-s3-%'"
        )
        await conn.close()


# ──────────────────────────────────────────────────────────────────
# S5 — org multi-workspace isolation: corpus, webhooks, analytics
# ──────────────────────────────────────────────────────────────────


async def test_s5_org_multi_workspace_isolation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import lighthouse.api.dependencies as deps

    ws_a, ws_b = "scenario-s5-team-a", "scenario-s5-team-b"
    conn = await asyncpg.connect(_DSN)
    g = _graph()
    pool = await asyncpg.create_pool(
        _DSN, min_size=1, max_size=2, statement_cache_size=0
    )
    try:
        await run_migrations(conn, embedding_dim=_DIM)
        await _cleanup_s5(conn)

        # ── corpus isolation ─────────────────────────────────────
        await g.upsert_document(
            name="Team A runbook",
            body="Alpha deployment pipeline: secrets rotation and "
            "canary rollout procedure for team alpha.",
            source="scenario-s5-doc-a",
            workspace_id=ws_a,
        )
        await g.upsert_document(
            name="Team B finance guide",
            body="Bravo billing invoicing ledger reconciliation steps "
            "for team bravo.",
            source="scenario-s5-doc-b",
            workspace_id=ws_b,
        )
        hits = await g.search(
            "secrets rotation canary", workspace_id=ws_a, use_reranker=False
        )
        assert {h.source for h in hits} == {"scenario-s5-doc-a"}
        # A's keyword in B's workspace, and vice versa → nothing leaks.
        assert (
            await g.search(
                "secrets rotation canary", workspace_id=ws_b, use_reranker=False
            )
            == []
        )
        assert (
            await g.search(
                "billing invoicing ledger", workspace_id=ws_a, use_reranker=False
            )
            == []
        )

        # ── webhook delivery isolation ───────────────────────────
        hook_a = await conn.fetchval(
            "INSERT INTO webhooks (url, secret, events, workspace_id) "
            "VALUES ($1, $2, ARRAY['*'], $3) RETURNING id",
            "https://a.example/hook",
            "secret-a",
            ws_a,
        )
        hook_b = await conn.fetchval(
            "INSERT INTO webhooks (url, secret, events, workspace_id) "
            "VALUES ($1, $2, ARRAY['*'], $3) RETURNING id",
            "https://b.example/hook",
            "secret-b",
            ws_b,
        )
        n = await emit_event(
            pool, "importer.run.completed", {"run": "r1"}, workspace_id=ws_a
        )
        assert n == 1
        deliveries = await conn.fetch(
            "SELECT webhook_id, workspace_id FROM webhook_deliveries "
            "WHERE webhook_id = ANY($1::uuid[])",
            [hook_a, hook_b],
        )
        assert len(deliveries) == 1
        assert deliveries[0]["webhook_id"] == hook_a
        assert deliveries[0]["workspace_id"] == ws_a
        # Remove the hooks before the app (and its delivery worker) boots.
        await conn.execute(
            "DELETE FROM webhooks WHERE workspace_id LIKE 'scenario-s5-%'"
        )

        # ── query_log rows in two workspaces (real QueryLogger SQL) ──
        assert deps._PG_POOL is None
        deps._PG_POOL = pool  # QueryLogger._pool resolves get_pg_pool()
        try:
            ql = QueryLogger()
            for ws, query, hit_count, sources in [
                (ws_a, "how do we rotate secrets?", 2, ["scenario-s5-doc-a"]),
                (ws_a, "unanswerable alpha question", 0, []),
                (ws_b, "billing reconciliation steps", 1, ["scenario-s5-doc-b"]),
            ]:
                await ql._insert(
                    workspace_id=ws,
                    query=query,
                    top_k=10,
                    hit_count=hit_count,
                    top_sources=sources,
                    summaries=[],
                    top_score=0.5 if hit_count else None,
                    api_key_id=None,
                    latency_ms=12,
                )
        finally:
            deps._PG_POOL = None
        # _insert swallows failures by design — verify the rows landed.
        assert (
            await conn.fetchval(
                "SELECT COUNT(*) FROM query_log "
                "WHERE workspace_id LIKE 'scenario-s5-%'"
            )
            == 3
        )

        # ── analytics over HTTP, scoped per workspace ────────────
        with _app_client(monkeypatch, tmp_path) as client:
            hdr_a = {"X-Workspace": ws_a}
            hdr_b = {"X-Workspace": ws_b}

            ov_a = client.get("/v1/analytics/overview", headers=hdr_a).json()
            assert ov_a["total_questions"] == 2
            assert ov_a["total_gaps"] == 1
            ov_b = client.get("/v1/analytics/overview", headers=hdr_b).json()
            assert ov_b["total_questions"] == 1
            assert ov_b["total_gaps"] == 0

            tq_a = client.get("/v1/analytics/top-queries", headers=hdr_a).json()
            assert {t["query"] for t in tq_a} == {
                "how do we rotate secrets?",
                "unanswerable alpha question",
            }
            tq_b = client.get("/v1/analytics/top-queries", headers=hdr_b).json()
            assert {t["query"] for t in tq_b} == {"billing reconciliation steps"}

            gaps_a = client.get("/v1/analytics/gaps", headers=hdr_a).json()
            assert [gap["query"] for gap in gaps_a] == [
                "unanswerable alpha question"
            ]
            assert client.get("/v1/analytics/gaps", headers=hdr_b).json() == []

            su_a = client.get("/v1/analytics/source-usage", headers=hdr_a).json()
            assert {s["source"] for s in su_a} == {"scenario-s5-doc-a"}
            su_b = client.get("/v1/analytics/source-usage", headers=hdr_b).json()
            assert {s["source"] for s in su_b} == {"scenario-s5-doc-b"}

            # ── gap triage PATCH: insert then ON CONFLICT update ──
            body = {"query": "Unanswerable ALPHA question", "status": "planned"}
            r1 = client.patch(
                "/v1/analytics/gaps/status", json=body, headers=hdr_a
            )
            assert r1.status_code == 200, r1.text
            assert r1.json()["status"] == "planned"
            # Still listed (planned is an open-ish status).
            gaps_a = client.get("/v1/analytics/gaps", headers=hdr_a).json()
            assert gaps_a[0]["status"] == "planned"

            body["status"] = "resolved"
            r2 = client.patch(
                "/v1/analytics/gaps/status", json=body, headers=hdr_a
            )
            assert r2.status_code == 200, r2.text
            assert r2.json()["status"] == "resolved"
            # Resolved gaps disappear from the default listing…
            assert client.get("/v1/analytics/gaps", headers=hdr_a).json() == []
            # …but show with include_resolved, carrying the new status.
            shown = client.get(
                "/v1/analytics/gaps",
                params={"include_resolved": "true"},
                headers=hdr_a,
            ).json()
            assert shown[0]["status"] == "resolved"

        # ON CONFLICT updated in place — exactly one triage row exists.
        triage = await conn.fetch(
            "SELECT status FROM coverage_gap_status WHERE workspace_id = $1",
            ws_a,
        )
        assert [t["status"] for t in triage] == ["resolved"]
        assert (
            await conn.fetchval(
                "SELECT COUNT(*) FROM coverage_gap_status WHERE workspace_id = $1",
                ws_b,
            )
            == 0
        )
    finally:
        deps._PG_POOL = None
        await _cleanup_s5(conn)
        await g.close()
        await pool.close()
        await conn.close()


async def _cleanup_s5(conn: asyncpg.Connection) -> None:
    await conn.execute("DELETE FROM chunks WHERE source LIKE 'scenario-s5-%'")
    await conn.execute(
        "DELETE FROM webhooks WHERE workspace_id LIKE 'scenario-s5-%'"
    )
    await conn.execute(
        "DELETE FROM query_log WHERE workspace_id LIKE 'scenario-s5-%'"
    )
    await conn.execute(
        "DELETE FROM coverage_gap_status WHERE workspace_id LIKE 'scenario-s5-%'"
    )


# ──────────────────────────────────────────────────────────────────
# S6 — brownfield upgrade from a 0005-era schema
# ──────────────────────────────────────────────────────────────────


async def test_s6_brownfield_upgrade() -> None:
    conn = await asyncpg.connect(_DSN)
    g = _graph()
    try:
        await run_migrations(conn, embedding_dim=_DIM)
        await conn.execute(
            "DELETE FROM chunks WHERE source LIKE 'scenario-s6-%'"
        )
        await conn.execute(
            "DELETE FROM webhooks WHERE url = 'https://legacy.example/hook'"
        )

        # Rewind to the 0005-era schema: drop everything 0006+ added and
        # forget those versions so the runner re-applies them.
        await conn.execute(
            "DELETE FROM schema_migrations WHERE version >= '0006'"
        )
        await conn.execute("DROP TABLE IF EXISTS query_log CASCADE")
        await conn.execute("DROP TABLE IF EXISTS coverage_gap_status CASCADE")
        await conn.execute("DROP TABLE IF EXISTS api_keys CASCADE")
        await conn.execute(
            "ALTER TABLE webhooks DROP COLUMN IF EXISTS workspace_id"
        )
        await conn.execute(
            "ALTER TABLE webhook_deliveries DROP COLUMN IF EXISTS workspace_id"
        )

        # Brownfield data, written with 0001–0005-era columns only.
        await g.upsert_document(
            name="Legacy ops handbook",
            body="Bluegreen deployment switchover procedure from the "
            "legacy ops handbook.",
            source="scenario-s6-legacy",
            workspace_id="public",
        )
        hook_id = await conn.fetchval(
            "INSERT INTO webhooks (url, secret) VALUES ($1, $2) RETURNING id",
            "https://legacy.example/hook",
            "legacy-secret",
        )

        # Upgrade: only the missing migrations apply, with no errors,
        # then a re-run is a no-op.
        applied = await run_migrations(conn, embedding_dim=_DIM)
        assert applied == _ALL_MIGRATIONS[5:]
        assert await run_migrations(conn, embedding_dim=_DIM) == []

        # The pre-existing webhook is grandfathered into 'public'.
        assert (
            await conn.fetchval(
                "SELECT workspace_id FROM webhooks WHERE id = $1", hook_id
            )
            == "public"
        )
        # Old chunks are still searchable post-upgrade.
        hits = await g.search(
            "bluegreen switchover procedure",
            workspace_id="public",
            use_reranker=False,
        )
        assert [h.source for h in hits] == ["scenario-s6-legacy"]
    finally:
        await conn.execute(
            "DELETE FROM chunks WHERE source LIKE 'scenario-s6-%'"
        )
        await conn.execute(
            "DELETE FROM webhooks WHERE url = 'https://legacy.example/hook'"
        )
        await g.close()
        await conn.close()


# ──────────────────────────────────────────────────────────────────
# Full HTTP smoke — keys → scoped search → 403 → analytics
# ──────────────────────────────────────────────────────────────────


async def test_http_smoke_keys_search_analytics(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    ws_a, ws_b = "scenario-h-team-a", "scenario-h-team-b"
    conn = await asyncpg.connect(_DSN)
    g = _graph()
    try:
        await run_migrations(conn, embedding_dim=_DIM)
        await _cleanup_http(conn)

        # The SAME body in both workspaces: proves search results are
        # scoped by the key's workspace, not by data being absent.
        shared = (
            "Oncall rotation handover checklist: pager escalation, "
            "incident commander handoff, postmortem owner."
        )
        await g.upsert_document(
            name="Team A incident runbook",
            body=shared,
            source="scenario-h-doc-a",
            workspace_id=ws_a,
        )
        await g.upsert_document(
            name="Team B incident runbook",
            body=shared,
            source="scenario-h-doc-b",
            workspace_id=ws_b,
        )
        await g.close()

        with _app_client(monkeypatch, tmp_path) as client:
            # Mint a key for team-a (admin surface, insecure-open).
            r = client.post(
                "/v1/keys/", json={"name": "ci"}, headers={"X-Workspace": ws_a}
            )
            assert r.status_code == 201, r.text
            created = r.json()
            secret = created["secret"]
            assert secret.startswith("lh_")
            assert created["workspace_id"] == ws_a
            bearer = {"Authorization": f"Bearer {secret}"}

            # The lh_ secret authenticates search, scoped to team-a.
            r = client.get(
                "/v1/search",
                params={"q": "oncall rotation handover"},
                headers=bearer,
            )
            assert r.status_code == 200, r.text
            sources = {h["source"] for h in r.json()["hits"]}
            assert sources == {"scenario-h-doc-a"}

            # A gap query through the same key (for analytics below).
            r = client.get(
                "/v1/search",
                params={"q": "zorblefrazz wibblequx"},
                headers=bearer,
            )
            assert r.status_code == 200
            assert r.json()["hits"] == []

            # Mismatched X-Workspace with that key → 403, never leaks.
            r = client.get(
                "/v1/search",
                params={"q": "oncall rotation handover"},
                headers={**bearer, "X-Workspace": ws_b},
            )
            assert r.status_code == 403

            # Keyless legacy path for team-b (auth not required) — its
            # search must not show up in team-a's analytics.
            r = client.get(
                "/v1/search",
                params={"q": "oncall rotation handover"},
                headers={"X-Workspace": ws_b},
            )
            assert r.status_code == 200
            assert {h["source"] for h in r.json()["hits"]} == {
                "scenario-h-doc-b"
            }

            # Analytics reflect ONLY team-a's two logged searches
            # (fire-and-forget inserts — poll briefly).
            ov_a = _poll_overview(client, ws_a, want_questions=2)
            assert ov_a["total_questions"] == 2, ov_a
            assert ov_a["total_gaps"] == 1
            ov_b = _poll_overview(client, ws_b, want_questions=1)
            assert ov_b["total_questions"] == 1, ov_b
            assert ov_b["total_gaps"] == 0

        # Billing-grade attribution: team-a rows carry the key id, and
        # the key was stamped as used.
        rows = await conn.fetch(
            "SELECT api_key_id FROM query_log WHERE workspace_id = $1", ws_a
        )
        assert len(rows) == 2
        assert all(str(r["api_key_id"]) == str(created["id"]) for r in rows)
        assert (
            await conn.fetchval(
                "SELECT last_used_at FROM api_keys WHERE id = $1::uuid",
                created["id"],
            )
            is not None
        )
    finally:
        await _cleanup_http(conn)
        await g.close()
        await conn.close()


async def _cleanup_http(conn: asyncpg.Connection) -> None:
    await conn.execute("DELETE FROM chunks WHERE source LIKE 'scenario-h-%'")
    await conn.execute(
        "DELETE FROM api_keys WHERE workspace_id LIKE 'scenario-h-%'"
    )
    await conn.execute(
        "DELETE FROM query_log WHERE workspace_id LIKE 'scenario-h-%'"
    )
    await conn.execute(
        "DELETE FROM coverage_gap_status WHERE workspace_id LIKE 'scenario-h-%'"
    )
