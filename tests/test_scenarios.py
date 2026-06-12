"""User-scenario journey tests.

Executable versions of the six journeys in docs/scenarios.md — solo
dev / team / organization × from-scratch / existing-docs. Each test
walks a journey end-to-end at the HTTP layer (fakes at the asyncpg
seam, same convention as tests/test_auth.py); real-Postgres versions
live in tests/integration/test_scenarios_pg.py.

These intentionally re-walk a few steps already unit-tested elsewhere:
the point is the *sequence* a user actually performs, so a regression
anywhere in the chain fails the journey that hits it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from lighthouse.api.dependencies import (
    _rate_limiter,
    get_graph,
    get_pg_pool,
    get_query_logger,
)
from lighthouse.api.main import create_app
from lighthouse.core.auth import KEY_PREFIX
from lighthouse.core.config import get_settings
from lighthouse.core.flat_graph import FlatHit
from tests.test_auth import FakePool, key_row

NOW = datetime.now(UTC)


class RecordingLogger:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def log(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


def make_client(fake_graph, pool, logger=None) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_graph] = lambda: fake_graph
    app.dependency_overrides[get_query_logger] = lambda: logger or RecordingLogger()
    app.dependency_overrides[get_pg_pool] = lambda: pool
    return TestClient(app)


def importer_row(type_: str = "url_list", *, secrets: bool, ws: str = "public"):
    return {
        "id": uuid4(), "type": type_, "name": "My docs", "description": None,
        "recipe": "docs", "config": {"urls": ["https://example.com"]},
        "secrets_enc": b"\x80enc" if secrets else None,
        "enabled": True, "status": "idle", "workspace_id": ws,
        "last_run_at": None, "last_error": None,
        "created_at": NOW, "updated_at": NOW,
    }


# ─────────────────────────────────────────────────────────────────
# S1 — solo dev, from scratch, zero keys
# ─────────────────────────────────────────────────────────────────


class TestS1SoloDevScratch:
    def test_keyless_loop_search_log_gap(self, fake_graph) -> None:
        """No tokens of any kind: search works on the public workspace,
        the question is logged, and an unanswered one becomes a gap."""
        logger = RecordingLogger()
        gaps_pool = FakePool([
            ("FROM query_log", [
                {"query_norm": "how do i deploy", "count": 1,
                 "last_asked_at": NOW, "status": "open"},
            ]),
        ])
        with make_client(fake_graph, gaps_pool, logger) as client:
            # 1. Search with docs present.
            fake_graph.search_hits = [
                FlatHit(node_id="n1", summary="Deploy guide", source="notes/deploy.md"),
            ]
            assert client.get("/v1/search", params={"q": "deploy"}).status_code == 200
            assert logger.calls[-1]["workspace_id"] == "public"
            assert logger.calls[-1]["hit_count"] == 1

            # 2. A question the notes can't answer → logged with 0 hits.
            fake_graph.search_hits = []
            assert client.get("/v1/search", params={"q": "how do i deploy"}).status_code == 200
            assert logger.calls[-1]["hit_count"] == 0

            # 3. It shows up on the Coverage gaps page (admin is open
            #    locally via LIGHTHOUSE_INSECURE_ADMIN from conftest).
            res = client.get("/v1/analytics/gaps")
            assert res.status_code == 200
            assert res.json()[0]["query"] == "how do i deploy"

    def test_admin_ui_reachable_without_any_config(self, fake_graph) -> None:
        with make_client(fake_graph, FakePool()) as client:
            assert client.get("/ui/").status_code == 200


# ─────────────────────────────────────────────────────────────────
# S2 — solo dev, existing docs: gap triage lifecycle
# ─────────────────────────────────────────────────────────────────


class TestS2ExistingDocs:
    def test_gap_triage_lifecycle(self, fake_graph) -> None:
        """Gap appears → triaged to planned → resolved; resolved gaps
        leave the default view (the operator's working loop)."""
        pool = FakePool([
            ("lower(btrim(query)) = $2", {"count": 3, "last_asked_at": NOW}),
            ("FROM query_log", []),
        ])
        with make_client(fake_graph, pool) as client:
            for status in ("planned", "resolved"):
                res = client.patch(
                    "/v1/analytics/gaps/status",
                    json={"query": "Webhooks Retry Policy", "status": status},
                )
                assert res.status_code == 200
                assert res.json()["status"] == status

            # Both PATCHes hit the same ON CONFLICT upsert with the
            # normalized cluster key.
            upserts = [
                (q, a) for q, a in pool.conn.executed
                if "INSERT INTO coverage_gap_status" in q
            ]
            assert len(upserts) == 2
            assert all(a[1] == "webhooks retry policy" for _, a in upserts)
            assert "ON CONFLICT" in upserts[0][0]

            # Default gaps view filters resolved/ignored out in SQL.
            client.get("/v1/analytics/gaps")
            gaps_q = next(
                q for q, _ in pool.conn.executed
                if "FROM query_log" in q and "JOIN" in q.upper()
            )
            assert "IN ('open', 'planned')" in gaps_q

    def test_importer_run_conflict_guard(self, fake_graph) -> None:
        """Clicking Run twice during a crawl must not start a second
        run — the second click is a clean 409."""
        running = importer_row(secrets=False)
        running["status"] = "running"
        pool = FakePool([("FROM importers", running)])
        with make_client(fake_graph, pool) as client:
            res = client.post(f"/v1/importers/{running['id']}/run")
        assert res.status_code == 409


# ─────────────────────────────────────────────────────────────────
# S3 — team, from scratch: shared instance, keys per member
# ─────────────────────────────────────────────────────────────────


class TestS3TeamScratch:
    def test_team_key_lifecycle(self, fake_graph, monkeypatch) -> None:
        """Operator locks the instance, issues keys, a member leaves."""
        monkeypatch.delenv("LIGHTHOUSE_INSECURE_ADMIN", raising=False)
        monkeypatch.setenv("LIGHTHOUSE_ADMIN_TOKEN", "ops-token")
        monkeypatch.setenv("LIGHTHOUSE_RETRIEVAL_AUTH_REQUIRED", "true")
        admin = {"Authorization": "Bearer ops-token", "X-Workspace": "team"}

        alice = key_row("team")
        alice.update({"created_at": NOW, "last_used_at": None, "revoked_at": None})
        pool = FakePool([("INSERT INTO api_keys", alice)])
        with make_client(fake_graph, pool) as client:
            # 1. Without the operator token nothing administrative works.
            assert client.post("/v1/keys/", json={"name": "alice"}).status_code == 401
            # 2. Operator issues a key.
            res = client.post("/v1/keys/", json={"name": "alice"}, headers=admin)
            assert res.status_code == 201
            secret = res.json()["secret"]
            assert secret.startswith(KEY_PREFIX)

            # 3. Keyless / spoofed retrieval is locked out.
            assert client.get("/v1/search", params={"q": "x"}).status_code == 401
            assert client.get(
                "/v1/search", params={"q": "x"}, headers={"X-Workspace": "team"}
            ).status_code == 401

            # 4. Alice's key works and pins the team workspace.
            pool.conn._responses = [("UPDATE api_keys", alice)]
            res = client.get(
                "/v1/search", params={"q": "x"},
                headers={"Authorization": f"Bearer {secret}"},
            )
            assert res.status_code == 200
            assert fake_graph.last_search_workspace == "team"

            # 5. Alice leaves — her key is revoked and stops working.
            revoked = dict(alice)
            revoked["revoked_at"] = NOW
            pool.conn._responses = [("revoked_at = NOW()", revoked)]
            assert client.delete(
                f"/v1/keys/{alice['id']}", headers=admin
            ).status_code == 200
            pool.conn._responses = [("UPDATE api_keys", None)]
            assert client.get(
                "/v1/search", params={"q": "x"},
                headers={"Authorization": f"Bearer {secret}"},
            ).status_code == 401

    def test_runaway_ci_job_gets_throttled(self, fake_graph, monkeypatch) -> None:
        monkeypatch.setenv("LIGHTHOUSE_SEARCH_RATE_LIMIT_PER_MINUTE", "3")
        get_settings.cache_clear()
        _rate_limiter.cache_clear()
        try:
            with make_client(fake_graph, FakePool()) as client:
                codes = [
                    client.get("/v1/search", params={"q": "x"}).status_code
                    for _ in range(5)
                ]
            assert codes == [200, 200, 200, 429, 429]
        finally:
            get_settings.cache_clear()
            _rate_limiter.cache_clear()


# ─────────────────────────────────────────────────────────────────
# S4 — team, existing knowledge: secret-bearing importers
# ─────────────────────────────────────────────────────────────────


class TestS4ExistingKnowledge:
    def test_secret_importer_without_master_key_fails_actionably(
        self, fake_graph, monkeypatch
    ) -> None:
        monkeypatch.delenv("LIGHTHOUSE_SECRETS_KEY", raising=False)
        # crypto caches the Fernet — make sure the cache is cold.
        from lighthouse.importers import crypto

        crypto._fernet.cache_clear()
        with make_client(fake_graph, FakePool()) as client:
            res = client.post("/v1/importers/", json={
                "type": "notion", "name": "Team Notion", "recipe": "notion",
                "config": {}, "secrets": {"integration_token": "secret-ntn-token"},
            })
        assert res.status_code == 500
        assert "LIGHTHOUSE_SECRETS_KEY" in res.json()["detail"]

    def test_secret_importer_encrypts_and_never_echoes(
        self, fake_graph, monkeypatch
    ) -> None:
        monkeypatch.setenv("LIGHTHOUSE_SECRETS_KEY", Fernet.generate_key().decode())
        from lighthouse.importers import crypto

        crypto._fernet.cache_clear()
        created = importer_row("notion", secrets=True)
        pool = FakePool([("INSERT INTO importers", created)])
        try:
            with make_client(fake_graph, pool) as client:
                res = client.post("/v1/importers/", json={
                    "type": "notion", "name": "Team Notion", "recipe": "notion",
                    "config": {}, "secrets": {"integration_token": "secret-ntn-token"},
                })
            assert res.status_code == 200
            body = res.json()
            # The read model exposes presence, never the value.
            assert body["has_secrets"] is True
            assert "secret-ntn-token" not in res.text
            # What reached the database is ciphertext, not the token.
            _, args = pool.conn.executed[0]
            stored = next(a for a in args if isinstance(a, bytes))
            assert b"secret-ntn-token" not in stored
        finally:
            crypto._fernet.cache_clear()


# ─────────────────────────────────────────────────────────────────
# S5 — organization: multi-team isolation matrix
# ─────────────────────────────────────────────────────────────────


class TestS5OrgWorkspaces:
    def test_key_of_team_a_cannot_touch_team_b(self, fake_graph) -> None:
        pool = FakePool([("UPDATE api_keys", key_row("team-a"))])
        with make_client(fake_graph, pool) as client:
            ok = client.get(
                "/v1/search", params={"q": "x"},
                headers={"Authorization": f"Bearer {KEY_PREFIX}a"},
            )
            assert ok.status_code == 200
            assert fake_graph.last_search_workspace == "team-a"

            for path in ("/v1/search?q=x", "/v1/fetch_source/some-id"):
                res = client.get(path, headers={
                    "Authorization": f"Bearer {KEY_PREFIX}a",
                    "X-Workspace": "team-b",
                })
                assert res.status_code == 403, path

    def test_admin_surfaces_scope_to_requested_workspace(self, fake_graph) -> None:
        """The operator hops between tenants via X-Workspace; every
        admin read carries that workspace into SQL."""
        pool = FakePool([
            ("FROM api_keys", []),
            ("FROM webhooks", []),
            ("FROM importers", []),
            ("FROM query_log", []),
        ])
        surfaces = [
            "/v1/keys/", "/v1/webhooks/", "/v1/importers/",
            "/v1/analytics/top-queries",
        ]
        with make_client(fake_graph, pool) as client:
            for path in surfaces:
                assert client.get(
                    path, headers={"X-Workspace": "team-b"}
                ).status_code == 200, path
        for q, args in pool.conn.executed:
            assert "team-b" in args, q


# ─────────────────────────────────────────────────────────────────
# S6 — organization: brownfield upgrade
# ─────────────────────────────────────────────────────────────────


class TestS6BrownfieldUpgrade:
    def test_legacy_keyless_contract_preserved(self, fake_graph) -> None:
        """Until the operator flips auth on, 0.0.1 clients keep working
        byte-for-byte: header-selected workspace, public default."""
        with make_client(fake_graph, FakePool()) as client:
            assert client.get("/v1/search", params={"q": "x"}).status_code == 200
            assert fake_graph.last_search_workspace == "public"
            client.get("/v1/search", params={"q": "x"},
                       headers={"X-Workspace": "legacy-tenant"})
            assert fake_graph.last_search_workspace == "legacy-tenant"

    def test_auth_flip_is_a_hard_switch(self, fake_graph, monkeypatch) -> None:
        monkeypatch.setenv("LIGHTHOUSE_RETRIEVAL_AUTH_REQUIRED", "true")
        with make_client(fake_graph, FakePool()) as client:
            assert client.get("/v1/search", params={"q": "x"}).status_code == 401

    async def test_pre_0009_webhooks_grandfathered_to_public(self) -> None:
        """emit_event without an explicit workspace targets 'public' —
        exactly where migration 0009 parked pre-existing subscriptions."""
        from lighthouse.webhooks.dispatcher import emit_event

        pool = FakePool([("SELECT id FROM webhooks", [{"id": uuid4()}])])
        await emit_event(pool, "importer.run.finished", {})
        _, select_args = pool.conn.executed[0]
        assert select_args[1] == "public"
        _, insert_args = pool.conn.executed[1]
        assert insert_args[-1] == "public"

    def test_per_workspace_require_auth_flag(self, fake_graph) -> None:
        """The mixed-mode fix: one workspace flips require_auth on and
        keyless callers lose it, while unflagged workspaces (and the
        instance default) stay open."""
        from lighthouse.core.auth import invalidate_workspace_auth_cache

        flagged_pool = FakePool([
            ("SELECT require_auth FROM workspaces", True),
            ("UPDATE api_keys", key_row("team-a")),
        ])
        with make_client(fake_graph, flagged_pool) as client:
            # Keyless → 401 for the flagged workspace…
            res = client.get(
                "/v1/search", params={"q": "x"},
                headers={"X-Workspace": "team-a"},
            )
            assert res.status_code == 401
            assert "team-a" in res.json()["detail"]
            # …but a key for it still works.
            res = client.get(
                "/v1/search", params={"q": "x"},
                headers={"Authorization": f"Bearer {KEY_PREFIX}a"},
            )
            assert res.status_code == 200

        invalidate_workspace_auth_cache()
        open_pool = FakePool([("SELECT require_auth FROM workspaces", None)])
        with make_client(fake_graph, open_pool) as client:
            assert client.get(
                "/v1/search", params={"q": "x"},
                headers={"X-Workspace": "team-open"},
            ).status_code == 200

    def test_workspace_registry_upsert_and_list(self, fake_graph) -> None:
        row = {"id": "team-a", "require_auth": True, "description": None,
               "created_at": NOW, "updated_at": NOW}
        pool = FakePool([
            ("INSERT INTO workspaces", row),
            ("FROM workspaces ORDER BY id", [row]),
        ])
        with make_client(fake_graph, pool) as client:
            res = client.put("/v1/workspaces/team-a", json={"require_auth": True})
            assert res.status_code == 200
            assert res.json()["require_auth"] is True
            upsert_q, args = pool.conn.executed[0]
            assert "ON CONFLICT (id) DO UPDATE" in upsert_q
            assert args[0] == "team-a"

            res = client.get("/v1/workspaces/")
            assert res.status_code == 200
            assert res.json()[0]["id"] == "team-a"

            # Garbage ids are rejected before touching SQL.
            assert client.put(
                "/v1/workspaces/bad%20id", json={}
            ).status_code == 422

    def test_dead_webhook_deliveries_requeue(self, fake_graph) -> None:
        pool = FakePool([("SET status = 'pending'", "UPDATE 2")])
        with make_client(fake_graph, pool) as client:
            res = client.post(
                f"/v1/webhooks/{uuid4()}/deliveries/requeue-dead",
                headers={"X-Workspace": "team-a"},
            )
        assert res.status_code == 200
        assert res.json() == {"requeued": 2}
        q, args = pool.conn.executed[0]
        assert "status = 'dead'" in q
        assert args[1] == "team-a"

    def test_gap_status_prune(self, fake_graph) -> None:
        pool = FakePool([("DELETE FROM coverage_gap_status", "DELETE 3")])
        with make_client(fake_graph, pool) as client:
            res = client.post(
                "/v1/analytics/gaps/prune?days=30",
                headers={"X-Workspace": "team-a"},
            )
        assert res.status_code == 200
        assert res.json() == {"pruned": 3}
        q, args = pool.conn.executed[0]
        assert "NOT EXISTS" in q
        assert args == ("team-a", 30)

    async def test_query_logger_accepts_injected_pool(self) -> None:
        """The DI seam the integration suite had to hack around: a
        custom pool_factory is honored end-to-end."""
        from lighthouse.core.query_log import QueryLogger

        pool = FakePool()
        logger = QueryLogger(pool_factory=lambda: pool)
        await logger._insert(
            workspace_id="w", query="q", top_k=5, hit_count=1,
            top_sources=["s"], summaries=[], top_score=None,
            api_key_id=None, latency_ms=1,
        )
        assert "INSERT INTO query_log" in pool.conn.executed[0][0]

    def test_usage_rollup_per_key_and_day(self, fake_graph) -> None:
        """The billing read-side: GET /v1/usage breaks the workspace's
        searches down per key (keyless legacy = null key) and per day."""
        kid = uuid4()
        pool = FakePool([
            ("LEFT JOIN api_keys", [
                {"api_key_id": kid, "key_name": "alice", "searches": 5,
                 "gaps": 1, "last_used_at": NOW},
                {"api_key_id": None, "key_name": None, "searches": 2,
                 "gaps": 0, "last_used_at": NOW},
            ]),
            ("date_trunc('day', created_at)", [{"day": NOW, "searches": 7}]),
        ])
        with make_client(fake_graph, pool) as client:
            res = client.get(
                "/v1/usage/?days=7", headers={"X-Workspace": "team-a"}
            )
        assert res.status_code == 200
        body = res.json()
        assert body["total_searches"] == 7
        assert body["by_key"][0]["key_name"] == "alice"
        assert body["by_key"][1]["api_key_id"] is None  # keyless traffic
        assert body["by_day"][0]["searches"] == 7
        # Both aggregates scoped to the requested workspace.
        for _, args in pool.conn.executed:
            assert args[0] == "team-a"

    def test_usage_attribution_flows_to_query_log(self, fake_graph) -> None:
        """Billing prerequisite: searches made with a key carry the key
        id into the analytics log."""
        key = key_row("team-a")
        logger = RecordingLogger()
        pool = FakePool([("UPDATE api_keys", key)])
        with make_client(fake_graph, pool, logger) as client:
            client.get("/v1/search", params={"q": "x"},
                       headers={"Authorization": f"Bearer {KEY_PREFIX}a"})
        assert logger.calls[0]["api_key_id"] == key["id"]
        assert logger.calls[0]["workspace_id"] == "team-a"
