"""Proposal pipeline tests.

Three layers covered:

- :class:`GitProposalStore` round-trips records through the filesystem
  including the markdown+frontmatter format.
- :func:`process_proposal` drives all four decision branches
  (accept / reject / escalate / errored) and verifies state +
  graph side-effects.
- The HTTP route round-trips: submit → poll → see final state.
"""

from __future__ import annotations

import asyncio

import pytest

from lighthouse.proposals.store import (
    GitProposalStore,
    ProposalRecord,
    new_proposal_id,
    utc_now,
)
from lighthouse.proposals.worker import process_proposal


# --- store ----------------------------------------------------------------


async def test_store_create_and_read_round_trip(proposal_store: GitProposalStore) -> None:
    record = ProposalRecord(
        id=new_proposal_id(),
        status="queued",
        type="add",
        content="FastAPI 0.115 supports lifespan context managers.",
        submitted_at=utc_now(),
        submitted_by="ci-bot",
        evidence=["https://fastapi.tiangolo.com/release-notes/"],
        rationale="release notes confirm",
    )
    await proposal_store.create(record)

    loaded = await proposal_store.read(record.id)
    assert loaded is not None
    assert loaded.id == record.id
    assert loaded.status == "queued"
    assert loaded.content == record.content
    assert loaded.evidence == record.evidence
    assert loaded.rationale == "release notes confirm"
    assert loaded.submitted_by == "ci-bot"


async def test_store_update_persists_decision_fields(proposal_store: GitProposalStore) -> None:
    record = ProposalRecord(
        id=new_proposal_id(),
        status="queued",
        type="add",
        content="x",
        submitted_at=utc_now(),
    )
    await proposal_store.create(record)

    record.status = "accepted"
    record.reason = "matches docs"
    record.decision_at = utc_now()
    record.episode_uuid = "episode-abc"
    await proposal_store.update(record, action="accepted")

    loaded = await proposal_store.read(record.id)
    assert loaded is not None
    assert loaded.status == "accepted"
    assert loaded.reason == "matches docs"
    assert loaded.episode_uuid == "episode-abc"


async def test_store_read_missing_returns_none(proposal_store: GitProposalStore) -> None:
    assert await proposal_store.read("not-a-real-id") is None


# --- worker ---------------------------------------------------------------


async def test_worker_accept_writes_to_graph(
    proposal_store: GitProposalStore, fake_graph, fake_librarian
) -> None:
    fake_librarian.next_decision = "accept"
    fake_librarian.next_reason = "matches official docs"

    record = ProposalRecord(
        id=new_proposal_id(),
        status="queued",
        type="add",
        content="Graphiti supports FalkorDB as of 0.29.",
        submitted_at=utc_now(),
        evidence=["https://github.com/getzep/graphiti"],
    )
    await proposal_store.create(record)

    await process_proposal(
        record.id, store=proposal_store, librarian=fake_librarian, graph=fake_graph
    )

    final = await proposal_store.read(record.id)
    assert final is not None
    assert final.status == "accepted"
    assert final.reason == "matches official docs"
    assert final.episode_uuid is not None
    assert len(fake_graph.ingested) == 1
    assert "Graphiti" in fake_graph.ingested[0]["body"]


async def test_worker_reject_leaves_graph_untouched(
    proposal_store: GitProposalStore, fake_graph, fake_librarian
) -> None:
    fake_librarian.next_decision = "reject"
    fake_librarian.next_reason = "no evidence"

    record = ProposalRecord(
        id=new_proposal_id(),
        status="queued",
        type="add",
        content="Some unsupported claim.",
        submitted_at=utc_now(),
    )
    await proposal_store.create(record)

    await process_proposal(
        record.id, store=proposal_store, librarian=fake_librarian, graph=fake_graph
    )

    final = await proposal_store.read(record.id)
    assert final is not None
    assert final.status == "rejected"
    assert final.reason == "no evidence"
    assert final.episode_uuid is None
    assert fake_graph.ingested == []


async def test_worker_escalate_marks_for_human(
    proposal_store: GitProposalStore, fake_graph, fake_librarian
) -> None:
    fake_librarian.next_decision = "escalate"
    fake_librarian.next_reason = "ambiguous"

    record = ProposalRecord(
        id=new_proposal_id(),
        status="queued",
        type="add",
        content="Edge case.",
        submitted_at=utc_now(),
    )
    await proposal_store.create(record)

    await process_proposal(
        record.id, store=proposal_store, librarian=fake_librarian, graph=fake_graph
    )

    final = await proposal_store.read(record.id)
    assert final is not None
    assert final.status == "escalated"
    assert fake_graph.ingested == []


async def test_worker_catches_librarian_exception(
    proposal_store: GitProposalStore, fake_graph, fake_librarian
) -> None:
    fake_librarian.raise_on_next = RuntimeError("model timeout")
    record = ProposalRecord(
        id=new_proposal_id(),
        status="queued",
        type="add",
        content="x",
        submitted_at=utc_now(),
    )
    await proposal_store.create(record)

    await process_proposal(
        record.id, store=proposal_store, librarian=fake_librarian, graph=fake_graph
    )

    final = await proposal_store.read(record.id)
    assert final is not None
    assert final.status == "errored"
    assert "model timeout" in (final.reason or "")


async def test_worker_skips_already_decided(
    proposal_store: GitProposalStore, fake_graph, fake_librarian
) -> None:
    record = ProposalRecord(
        id=new_proposal_id(),
        status="accepted",  # already decided
        type="add",
        content="x",
        submitted_at=utc_now(),
        episode_uuid="existing-episode",
    )
    await proposal_store.create(record)

    await process_proposal(
        record.id, store=proposal_store, librarian=fake_librarian, graph=fake_graph
    )

    # Librarian must not have been called, graph must not have been touched.
    assert fake_librarian.calls == []
    assert fake_graph.ingested == []


# --- HTTP round-trip ------------------------------------------------------


async def _wait_for_status(client, proposal_id: str, *, timeout: float = 2.0) -> dict:
    """Poll /v1/proposals/:id until status leaves 'queued'/'evaluating'
    or the budget runs out. The route fires a background asyncio task
    on submit; we let the loop tick until that task settles."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        r = client.get(f"/v1/proposals/{proposal_id}")
        assert r.status_code == 200
        body = r.json()
        if body["status"] not in {"queued", "evaluating"}:
            return body
        await asyncio.sleep(0.02)
    pytest.fail(f"proposal {proposal_id} did not settle within {timeout}s")


async def test_propose_then_poll_round_trip(
    client, fake_graph, fake_librarian
) -> None:
    fake_librarian.next_decision = "accept"
    fake_librarian.next_reason = "matches docs"

    r = client.post(
        "/v1/propose",
        json={
            "type": "add",
            "content": "FastAPI supports lifespan in 0.115.",
            "evidence": ["https://fastapi.tiangolo.com/release-notes/"],
            "rationale": "release notes",
            "submitted_by": "smoke-test",
        },
    )
    assert r.status_code == 202
    receipt = r.json()
    assert receipt["status"] == "queued"
    proposal_id = receipt["proposal_id"]

    final = await _wait_for_status(client, proposal_id)
    assert final["status"] == "accepted"
    assert final["reason"] == "matches docs"
    assert final["episode_uuid"] is not None
    assert final["submitted_by"] == "smoke-test"
    assert len(fake_graph.ingested) == 1


async def test_get_proposal_404_for_missing(client) -> None:
    r = client.get("/v1/proposals/does-not-exist")
    assert r.status_code == 404
