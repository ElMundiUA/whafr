"""Smoke tests — boot the API in-process via TestClient and verify the
routes are wired up. These don't talk to FalkorDB; the graph layer is
exercised end-to-end in a separate integration suite (TBD)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from lighthouse.api.main import app
from lighthouse.librarian.agent import _parse_decision

client = TestClient(app)


def test_health_returns_ok() -> None:
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_search_returns_empty_stub() -> None:
    r = client.get("/search", params={"q": "anything", "top_k": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "anything"
    assert body["hits"] == []


def test_search_rejects_empty_query() -> None:
    # Pydantic validation should kick in on q="".
    r = client.get("/search", params={"q": ""})
    assert r.status_code == 422


def test_propose_without_key_when_auth_disabled_succeeds(monkeypatch) -> None:
    # Default .env.example ships an empty key → auth disabled → anyone
    # can propose. Explicit test so the "empty key means disabled"
    # contract is pinned, not accidental.
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


def test_propose_with_wrong_key_rejected(monkeypatch) -> None:
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
