"""Search analytics: query logging on /v1/search, the /v1/analytics
aggregates, and the built-in admin UI mount.

The Postgres layer is faked at the asyncpg-pool seam (the same seam
``get_pg_pool`` exposes through ``dependency_overrides``) — these are
unit tests of route behaviour, not of SQL. Integration coverage of the
real queries lives with the rest of the PG-backed integration tests.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from lighthouse.api.dependencies import (
    get_graph,
    get_pg_pool,
    get_query_logger,
)
from lighthouse.api.main import create_app
from lighthouse.core.flat_graph import FlatHit
from lighthouse.core.query_log import QueryLogger

NOW = datetime.now(UTC)


class RecordingQueryLogger:
    """Stands in for QueryLogger — records calls instead of inserting."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def log(self, **kwargs: object) -> None:
        self.calls.append(kwargs)


class FakeConn:
    """Minimal asyncpg-connection stand-in fed from canned results."""

    def __init__(self, results: dict[str, object]) -> None:
        self._results = results
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    async def fetchrow(self, query: str, *args: object) -> object:
        self.executed.append((query, args))
        return self._results["fetchrow"]

    async def fetch(self, query: str, *args: object) -> object:
        self.executed.append((query, args))
        return self._results["fetch"]

    async def execute(self, query: str, *args: object) -> None:
        self.executed.append((query, args))


class FakePool:
    def __init__(self, results: dict[str, object]) -> None:
        self.conn = FakeConn(results)

    def acquire(self) -> FakePool:
        return self

    async def __aenter__(self) -> FakeConn:
        return self.conn

    async def __aexit__(self, *exc: object) -> None:
        return None


@pytest.fixture
def recording_logger() -> RecordingQueryLogger:
    return RecordingQueryLogger()


def make_client(fake_graph, recording_logger, pool=None) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_graph] = lambda: fake_graph
    app.dependency_overrides[get_query_logger] = lambda: recording_logger
    if pool is not None:
        app.dependency_overrides[get_pg_pool] = lambda: pool
    return TestClient(app)


# ───────────────────────── query logging ─────────────────────────


def test_search_logs_query(fake_graph, recording_logger) -> None:
    fake_graph.search_hits = [
        FlatHit(node_id="n1", summary="s1", source="https://a.example"),
        FlatHit(node_id="n2", summary="s2", source="https://b.example"),
        FlatHit(node_id="n3", summary="s3", source="https://a.example"),
    ]
    with make_client(fake_graph, recording_logger) as client:
        res = client.get(
            "/v1/search",
            params={"q": "How do CRDTs work?", "top_k": 5},
            headers={"X-Workspace": "acme"},
        )
    assert res.status_code == 200
    assert len(recording_logger.calls) == 1
    call = recording_logger.calls[0]
    assert call["workspace_id"] == "acme"
    assert call["query"] == "How do CRDTs work?"
    assert call["top_k"] == 5
    assert call["hit_count"] == 3
    # Sources are deduped, rank order preserved.
    assert call["top_sources"] == ["https://a.example", "https://b.example"]
    assert call["summaries"] == ["s1", "s2", "s3"]
    assert isinstance(call["latency_ms"], int)


def test_search_zero_hits_logged(fake_graph, recording_logger) -> None:
    fake_graph.search_hits = []
    with make_client(fake_graph, recording_logger) as client:
        res = client.get("/v1/search", params={"q": "unanswerable"})
    assert res.status_code == 200
    assert recording_logger.calls[0]["hit_count"] == 0


async def test_real_logger_marks_gap_and_survives_pg_failure(monkeypatch) -> None:
    """The real QueryLogger computes gap=hit_count==0 and never raises,
    even when the pool itself blows up."""
    pool = FakePool({})
    logger = QueryLogger()

    async def fake_pool():
        return pool

    monkeypatch.setattr("lighthouse.api.dependencies.get_pg_pool", fake_pool)
    await logger._insert(
        workspace_id="public", query="q", top_k=10,
        hit_count=0, top_sources=[], summaries=[],
        top_score=None, api_key_id=None, latency_ms=3,
    )
    query, args = pool.conn.executed[0]
    assert "INSERT INTO query_log" in query
    assert args[6] is True  # gap
    assert args[8] is None  # useful_score (classifier off)

    async def broken_pool():
        raise RuntimeError("no PG")

    monkeypatch.setattr("lighthouse.api.dependencies.get_pg_pool", broken_pool)
    # Must not raise.
    await logger._insert(
        workspace_id="public", query="q", top_k=10,
        hit_count=1, top_sources=["s"], summaries=["s"],
        top_score=0.03, api_key_id=None, latency_ms=3,
    )


@pytest.mark.parametrize(
    ("scores", "expect_gap", "expect_useful"),
    [
        ([1, 2, 1], True, pytest.approx(4 / 3)),   # weak hits → uncertain gap
        ([4, 5, 3], False, pytest.approx(4.0)),    # useful hits → not a gap
    ],
)
async def test_classifier_marks_uncertain_answers(
    monkeypatch, scores, expect_gap, expect_useful
) -> None:
    """With the classifier on, hits rated below USEFUL_THRESHOLD flag
    the search as a gap even though hit_count > 0."""
    pool = FakePool({})
    logger = QueryLogger()

    async def fake_pool():
        return pool

    async def fake_score_hits(query, summaries):
        return scores[: len(summaries)]

    class FakeSettings:
        lighthouse_gap_classifier_enabled = True

    monkeypatch.setattr("lighthouse.api.dependencies.get_pg_pool", fake_pool)
    monkeypatch.setattr("lighthouse.core.query_log.score_hits", fake_score_hits)
    monkeypatch.setattr("lighthouse.core.query_log.get_settings", FakeSettings)
    await logger._insert(
        workspace_id="public", query="q", top_k=10,
        hit_count=3, top_sources=["s"], summaries=["a", "b", "c"],
        top_score=0.03, api_key_id=None, latency_ms=3,
    )
    _, args = pool.conn.executed[0]
    assert args[6] is expect_gap   # gap
    assert args[8] == expect_useful  # useful_score


# ───────────────────────── analytics routes ─────────────────────────


def test_overview(fake_graph, recording_logger) -> None:
    pool = FakePool({
        "fetchrow": {
            "total_questions": 10, "total_gaps": 2, "total_uncertain": 1,
            "avg_useful_score": 3.8, "avg_latency_ms": 41.5,
        },
        "fetch": [{"day": NOW, "questions": 10, "gaps": 2}],
    })
    with make_client(fake_graph, recording_logger, pool) as client:
        res = client.get("/v1/analytics/overview?days=7")
    assert res.status_code == 200
    body = res.json()
    assert body["total_questions"] == 10
    assert body["gap_rate"] == pytest.approx(0.2)
    assert body["total_uncertain"] == 1
    assert body["avg_useful_score"] == pytest.approx(3.8)
    assert len(body["timeseries"]) == 1


def test_gaps_and_status_update(fake_graph, recording_logger) -> None:
    pool = FakePool({
        "fetch": [
            {"query_norm": "what is xyz", "count": 4, "last_asked_at": NOW, "status": "open"},
        ],
        "fetchrow": {"count": 4, "last_asked_at": NOW},
    })
    with make_client(fake_graph, recording_logger, pool) as client:
        res = client.get("/v1/analytics/gaps")
        assert res.status_code == 200
        assert res.json()[0]["query"] == "what is xyz"

        res = client.patch(
            "/v1/analytics/gaps/status",
            json={"query": "  What is XYZ ", "status": "planned"},
        )
        assert res.status_code == 200
        assert res.json()["status"] == "planned"
    upsert, args = next(
        (q, a) for q, a in pool.conn.executed
        if "INSERT INTO coverage_gap_status" in q
    )
    assert "ON CONFLICT" in upsert
    # Normalization applied before persisting.
    assert args[1] == "what is xyz"


def test_gap_status_rejects_unknown(fake_graph, recording_logger) -> None:
    with make_client(fake_graph, recording_logger, FakePool({})) as client:
        res = client.patch(
            "/v1/analytics/gaps/status",
            json={"query": "x", "status": "wontfix"},
        )
    assert res.status_code == 422


def test_source_usage_and_top_queries(fake_graph, recording_logger) -> None:
    pool = FakePool({
        "fetch": [
            {"source": "https://a", "refs": 7, "last_referenced_at": NOW,
             "query_norm": "q", "count": 7, "gap_count": 1, "avg_hits": 2.5,
             "last_asked_at": NOW},
        ],
    })
    with make_client(fake_graph, recording_logger, pool) as client:
        res = client.get("/v1/analytics/source-usage")
        assert res.status_code == 200
        assert res.json()[0]["references"] == 7

        res = client.get("/v1/analytics/top-queries")
        assert res.status_code == 200
        assert res.json()[0]["count"] == 7


def test_analytics_requires_admin_token(
    fake_graph, recording_logger, monkeypatch
) -> None:
    monkeypatch.setenv("LIGHTHOUSE_ADMIN_TOKEN", "sekrit")
    pool = FakePool({
        "fetchrow": {
            "total_questions": 0, "total_gaps": 0, "total_uncertain": 0,
            "avg_useful_score": None, "avg_latency_ms": None,
        },
        "fetch": [],
    })
    with make_client(fake_graph, recording_logger, pool) as client:
        assert client.get("/v1/analytics/overview").status_code == 401
        res = client.get(
            "/v1/analytics/overview",
            headers={"Authorization": "Bearer sekrit"},
        )
        assert res.status_code == 200


# ───────────────────────── admin UI mount ─────────────────────────


def test_admin_ui_served(fake_graph, recording_logger) -> None:
    with make_client(fake_graph, recording_logger) as client:
        res = client.get("/ui/")
        assert res.status_code == 200
        assert "Lighthouse" in res.text
        assert res.headers["content-type"].startswith("text/html")
        assert client.get("/ui/app.js").status_code == 200
        assert client.get("/ui/style.css").status_code == 200
