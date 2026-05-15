"""Proposal queue + recovery tests.

The queue is the durability layer between the route and the worker. It
must:

- bootstrap: re-enqueue any proposal stranded in ``queued`` /
  ``evaluating`` by a prior crash
- submit: schedule a worker, dedup against in-flight tasks
- drain: block until in-flight tasks settle (for clean shutdown)

We don't test the bounded-concurrency semaphore directly — that's a
property of the semaphore itself. We do test the durability contract,
because that's the bug we'd ship if it broke.
"""

from __future__ import annotations

import asyncio

import pytest

from lighthouse.proposals.queue import ProposalQueue
from lighthouse.proposals.store import (
    ProposalRecord,
    new_proposal_id,
    utc_now,
)


async def _make_record(store, *, status: str = "queued") -> ProposalRecord:
    record = ProposalRecord(
        id=new_proposal_id(),
        status=status,  # type: ignore[arg-type]
        type="add",
        content="A fresh fact about FastAPI.",
        submitted_at=utc_now(),
        evidence=["https://fastapi.tiangolo.com"],
    )
    await store.create(record)
    return record


async def test_store_list_pending_includes_queued_and_evaluating(
    proposal_store,
) -> None:
    queued = await _make_record(proposal_store, status="queued")
    evaluating = await _make_record(proposal_store, status="evaluating")
    accepted = await _make_record(proposal_store, status="accepted")
    rejected = await _make_record(proposal_store, status="rejected")

    pending = await proposal_store.list_pending()
    ids = {r.id for r in pending}
    assert queued.id in ids
    assert evaluating.id in ids
    assert accepted.id not in ids
    assert rejected.id not in ids


async def test_queue_submit_runs_worker(
    proposal_store, fake_librarian, fake_graph
) -> None:
    fake_librarian.next_decision = "accept"
    record = await _make_record(proposal_store)

    queue = ProposalQueue(
        store=proposal_store, librarian=fake_librarian, graph=fake_graph
    )
    await queue.submit(record.id)
    await queue.drain()  # wait for the worker to settle

    final = await proposal_store.read(record.id)
    assert final is not None
    assert final.status == "accepted"
    assert final.episode_uuid is not None


async def test_queue_submit_is_idempotent_for_in_flight(
    proposal_store, fake_librarian, fake_graph
) -> None:
    """Submitting the same id twice while the first task is in flight
    must not spawn a duplicate worker — we'd double-charge the LLM and
    risk racing on the store file."""
    # Slow down the librarian so the second submit can interleave.
    real_eval = fake_librarian.evaluate_proposal
    started = asyncio.Event()
    proceed = asyncio.Event()

    async def slow_eval(**kwargs):
        started.set()
        await proceed.wait()
        return await real_eval(**kwargs)

    fake_librarian.evaluate_proposal = slow_eval  # type: ignore[method-assign]
    fake_librarian.next_decision = "accept"

    record = await _make_record(proposal_store)
    queue = ProposalQueue(
        store=proposal_store, librarian=fake_librarian, graph=fake_graph
    )
    await queue.submit(record.id)
    await started.wait()

    # Second submit while the first is mid-flight — should no-op.
    await queue.submit(record.id)
    proceed.set()
    await queue.drain()

    # Only one librarian call recorded.
    assert len(fake_librarian.calls) == 1


async def test_queue_bootstrap_requeues_evaluating(
    proposal_store, fake_librarian, fake_graph
) -> None:
    """A proposal left in ``evaluating`` by a crashed worker must be
    rerun on bootstrap — that's the entire point of persistence."""
    fake_librarian.next_decision = "accept"
    stuck = await _make_record(proposal_store, status="evaluating")

    queue = ProposalQueue(
        store=proposal_store, librarian=fake_librarian, graph=fake_graph
    )
    n = await queue.bootstrap()
    await queue.drain()

    assert n == 1
    final = await proposal_store.read(stuck.id)
    assert final is not None
    assert final.status == "accepted"
    assert len(fake_librarian.calls) == 1


async def test_queue_bootstrap_skips_decided(
    proposal_store, fake_librarian, fake_graph
) -> None:
    await _make_record(proposal_store, status="accepted")
    await _make_record(proposal_store, status="rejected")

    queue = ProposalQueue(
        store=proposal_store, librarian=fake_librarian, graph=fake_graph
    )
    n = await queue.bootstrap()
    await queue.drain()

    assert n == 0
    assert fake_librarian.calls == []


async def test_queue_drain_with_no_in_flight_returns_immediately(
    proposal_store, fake_librarian, fake_graph
) -> None:
    queue = ProposalQueue(
        store=proposal_store, librarian=fake_librarian, graph=fake_graph
    )
    # Should not hang.
    await asyncio.wait_for(queue.drain(), timeout=0.5)


# End-to-end route → queue → worker is exercised by
# test_proposals.test_propose_then_poll_round_trip, which uses HTTP
# polling rather than direct awaits — that pattern works across the
# TestClient/test event-loop boundary. We don't duplicate it here;
# this module focuses on the queue's own contract.
