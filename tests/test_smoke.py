"""Smoke tests — boot the API in-process via TestClient against a fake
graph. The real pgvector backend is exercised in
``tests/integration/`` (gated behind ``LIGHTHOUSE_TEST_PG_URL``)."""

from __future__ import annotations

from datetime import UTC, datetime

from lighthouse.core.flat_graph import FlatHit
from lighthouse.librarian.agent import _parse_decision


def test_health_returns_ok(client) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_search_projects_hits(client, fake_graph) -> None:
    fake_graph.search_hits = [
        FlatHit(
            node_id="chunk-1",
            summary="FastAPI 0.115 supports lifespan context managers.",
            source="https://fastapi.tiangolo.com/",
            published_at=datetime(2025, 1, 1, tzinfo=UTC),
            episode_ids=["chunk-1"],
        ),
        FlatHit(
            node_id="chunk-2",
            summary="Pydantic v2 added ConfigDict.",
            source="https://docs.pydantic.dev/",
        ),
    ]

    r = client.get("/search", params={"q": "fastapi lifespan", "top_k": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "fastapi lifespan"
    assert len(body["hits"]) == 2
    assert body["hits"][0]["node_id"] == "chunk-1"
    assert "lifespan" in body["hits"][0]["summary"]
    assert body["hits"][0]["source"] == "https://fastapi.tiangolo.com/"
    assert body["hits"][0]["valid_from"] == "2025-01-01T00:00:00+00:00"
    # No entity layer in flat-RAG.
    assert body["hits"][0]["source_node_id"] is None


def test_search_respects_top_k(client, fake_graph) -> None:
    fake_graph.search_hits = [
        FlatHit(node_id=f"c-{i}", summary=f"chunk {i}", source="s")
        for i in range(10)
    ]
    r = client.get("/search", params={"q": "anything", "top_k": 3})
    assert r.status_code == 200
    assert len(r.json()["hits"]) == 3


def test_search_rejects_empty_query(client) -> None:
    r = client.get("/search", params={"q": ""})
    assert r.status_code == 422


def test_fetch_entity_is_inert_on_flat(client) -> None:
    # Flat-RAG has no entity layer — fetch_entity always 404s.
    r = client.get("/fetch/n-1")
    assert r.status_code == 404


def test_fetch_missing_returns_404(client) -> None:
    r = client.get("/fetch/does-not-exist")
    assert r.status_code == 404


def test_propose_without_key_when_auth_disabled_succeeds(client, monkeypatch) -> None:
    from lighthouse.core import config

    config.get_settings.cache_clear()
    monkeypatch.setenv("LIGHTHOUSE_PROPOSAL_API_KEY", "")

    r = client.post(
        "/v1/propose",
        json={
            "type": "add",
            "content": "FastAPI 0.115 supports lifespan context managers.",
            "evidence": ["https://fastapi.tiangolo.com/release-notes/"],
            "rationale": "release notes",
        },
    )
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued"
    assert body["proposal_id"]
    config.get_settings.cache_clear()


def test_propose_with_wrong_key_rejected(client, monkeypatch) -> None:
    from lighthouse.core import config

    config.get_settings.cache_clear()
    monkeypatch.setenv("LIGHTHOUSE_PROPOSAL_API_KEY", "correct-secret")

    r = client.post(
        "/v1/propose",
        headers={"X-Lighthouse-Key": "wrong-secret"},
        json={"type": "add", "content": "anything"},
    )
    assert r.status_code == 401
    config.get_settings.cache_clear()


def test_librarian_parse_decision_happy_path() -> None:
    decision, reason = _parse_decision("accept | matches docs, no dup")
    assert decision == "accept"
    assert reason == "matches docs, no dup"


def test_librarian_parse_decision_falls_back_to_escalate_on_unparseable() -> None:
    decision, _ = _parse_decision("I'm not sure about this one")
    assert decision == "escalate"
