"""Proposal worker.

A single async function — :func:`process_proposal` — that picks a
``queued`` record out of the store, runs the Librarian, and applies
the decision:

- ``accept`` — write to the graph via ``upsert_episode``, store
  the resulting episode uuid on the record
- ``reject`` — leave the graph untouched, store the librarian's reason
- ``escalate`` — leave the graph untouched, status flips to
  ``escalated`` for a human reviewer to pick up

This is the minimal end-to-end loop. A real queue (Temporal/Prefect,
per the ТЗ) wraps this same function later; the seam is
``process_proposal(id, *, store, librarian, graph)``.
"""

from __future__ import annotations

import logging

from lighthouse.core.graph import KnowledgeGraph
from lighthouse.librarian.agent import Librarian
from lighthouse.proposals.store import GitProposalStore, utc_now

logger = logging.getLogger(__name__)


async def process_proposal(
    proposal_id: str,
    *,
    store: GitProposalStore,
    librarian: Librarian,
    graph: KnowledgeGraph,
) -> None:
    """Drive one proposal through the full pipeline.

    Idempotent in the sense that re-invoking on an already-decided
    proposal short-circuits; we don't re-call the Librarian on an
    accepted/rejected record. The error path catches *anything*
    so a bad LLM response doesn't leave a proposal stuck in
    ``evaluating`` forever — it flips to ``errored`` with the
    exception message as reason.
    """
    record = await store.read(proposal_id)
    if record is None:
        logger.warning("proposal %s not found — worker bailing", proposal_id)
        return
    if record.status != "queued":
        logger.info(
            "proposal %s already in state %s — skipping",
            proposal_id,
            record.status,
        )
        return

    record.status = "evaluating"
    await store.update(record, action="evaluating")

    try:
        decision, reason = await librarian.evaluate_proposal(
            proposal_type=record.type,
            content=record.content,
            evidence=record.evidence,
            rationale=record.rationale,
            target_node_id=record.target_node_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("librarian errored on proposal %s", proposal_id)
        record.status = "errored"
        record.reason = f"librarian error: {exc}"
        record.decision_at = utc_now()
        await store.update(record, action="errored")
        return

    record.reason = reason
    record.decision_at = utc_now()

    if decision == "accept":
        try:
            episode_uuid = await graph.upsert_episode(
                name=f"proposal-{proposal_id[:8]}",
                body=record.content,
                source=f"proposal:{record.submitted_by}",
                reference_time=record.submitted_at,
                workspace_id="public",
            )
            record.episode_uuid = episode_uuid
            record.status = "accepted"
            await store.update(record, action="accepted")
        except Exception as exc:  # noqa: BLE001
            logger.exception("graph upsert failed for proposal %s", proposal_id)
            record.status = "errored"
            record.reason = f"graph upsert failed: {exc}"
            await store.update(record, action="errored")
        return

    if decision == "reject":
        record.status = "rejected"
        await store.update(record, action="rejected")
        return

    # escalate
    record.status = "escalated"
    await store.update(record, action="escalated")
