"""Smoke tests — boot the API in-process via TestClient against a fake
graph. The real graph backend is exercised in
``tests/integration/test_neo4j.py`` (gated behind
``LIGHTHOUSE_INTEGRATION=1``)."""

from __future__ import annotations

from datetime import UTC, datetime

from lighthouse.core.graph import GraphNode, GraphSearchHit
from lighthouse.librarian.agent import _parse_decision


def test_health_returns_ok(client) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_search_projects_graph_hits(client, fake_graph) -> None:
    fake_graph.search_hits = [
        GraphSearchHit(
            node_id="edge-1",
            summary="FastAPI 0.115 supports lifespan context managers.",
            source_node_uuid="node-a",
            target_node_uuid="node-b",
            valid_from=datetime(2025, 1, 1, tzinfo=UTC),
        ),
        GraphSearchHit(
            node_id="edge-2",
            summary="Pydantic v2 added ConfigDict.",
        ),
    ]

    r = client.get("/search", params={"q": "fastapi lifespan", "top_k": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "fastapi lifespan"
    assert len(body["hits"]) == 2
    assert body["hits"][0]["node_id"] == "edge-1"
    assert "lifespan" in body["hits"][0]["summary"]
    assert body["hits"][0]["source_node_id"] == "node-a"
    assert body["hits"][0]["valid_from"] == "2025-01-01T00:00:00+00:00"


def test_search_respects_top_k(client, fake_graph) -> None:
    fake_graph.search_hits = [
        GraphSearchHit(node_id=f"e-{i}", summary=f"fact {i}") for i in range(10)
    ]
    r = client.get("/search", params={"q": "anything", "top_k": 3})
    assert r.status_code == 200
    assert len(r.json()["hits"]) == 3


def test_search_rejects_empty_query(client) -> None:
    r = client.get("/search", params={"q": ""})
    assert r.status_code == 422


def test_fetch_returns_node(client, fake_graph) -> None:
    fake_graph.nodes["n-1"] = GraphNode(
        node_id="n-1",
        name="FastAPI",
        summary="An async Python web framework.",
        labels=["Entity", "Framework"],
        attributes={"language": "python"},
    )
    r = client.get("/fetch/n-1")
    assert r.status_code == 200
    body = r.json()
    assert body["node_id"] == "n-1"
    assert body["name"] == "FastAPI"
    assert "Framework" in body["labels"]
    assert body["attributes"]["language"] == "python"


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
