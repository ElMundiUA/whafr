"""Proposal pipeline endpoints.

``POST /v1/propose`` writes a proposal to the git-backed store and
fires the librarian worker as a fire-and-forget asyncio task. The
endpoint returns ``202 Accepted`` immediately with the proposal id —
clients poll ``GET /v1/proposals/:id`` for the decision.

Auth: a single shared API key on ``X-Lighthouse-Key``. Empty key in
config means auth disabled (local dev). Read endpoint is also gated
because proposal content can be sensitive (project-specific evidence).
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from lighthouse.api.dependencies import (
    get_proposal_queue,
    get_proposal_store,
)
from lighthouse.core.config import get_settings
from lighthouse.proposals.queue import ProposalQueue
from lighthouse.proposals.store import (
    GitProposalStore,
    ProposalRecord,
    ProposalStatus,
    new_proposal_id,
    utc_now,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["proposal"])

_api_key_header = APIKeyHeader(name="X-Lighthouse-Key", auto_error=False)


def _require_api_key(key: Annotated[str | None, Depends(_api_key_header)]) -> str:
    expected = get_settings().lighthouse_proposal_api_key
    if not expected:
        return "anonymous"
    if not key or key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing X-Lighthouse-Key",
        )
    return key


class Proposal(BaseModel):
    type: Literal["add", "correct", "deprecate"]
    target_node_id: str | None = Field(
        default=None,
        description="For 'correct'/'deprecate' — node being changed.",
    )
    content: str = Field(
        min_length=1,
        description="The proposed fact or correction, in natural language.",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="Sources backing the claim — URLs, logs, version refs.",
    )
    rationale: str = ""
    submitted_by: str = Field(
        default="anonymous",
        description="Free-form attribution tag — stored verbatim, never trusted.",
    )


class ProposalReceipt(BaseModel):
    proposal_id: str
    status: ProposalStatus = "queued"


class ProposalState(BaseModel):
    proposal_id: str
    status: ProposalStatus
    type: Literal["add", "correct", "deprecate"]
    content: str
    submitted_at: str
    submitted_by: str
    target_node_id: str | None = None
    evidence: list[str] = Field(default_factory=list)
    rationale: str = ""
    decision_at: str | None = None
    reason: str | None = None
    episode_uuid: str | None = None


@router.post(
    "/v1/propose",
    response_model=ProposalReceipt,
    status_code=status.HTTP_202_ACCEPTED,
)
async def propose(
    proposal: Proposal,
    store: Annotated[GitProposalStore, Depends(get_proposal_store)],
    queue: Annotated[ProposalQueue, Depends(get_proposal_queue)],
    _: Annotated[str, Depends(_require_api_key)],
) -> ProposalReceipt:
    proposal_id = new_proposal_id()
    record = ProposalRecord(
        id=proposal_id,
        status="queued",
        type=proposal.type,
        content=proposal.content,
        submitted_at=utc_now(),
        submitted_by=proposal.submitted_by,
        target_node_id=proposal.target_node_id,
        evidence=list(proposal.evidence),
        rationale=proposal.rationale,
    )
    await store.create(record)

    # Hand off to the queue. The queue bounds concurrency, tracks
    # in-flight workers for graceful shutdown, and is the unit that
    # gets bootstrapped on app startup to recover stranded proposals.
    await queue.submit(proposal_id)

    return ProposalReceipt(proposal_id=proposal_id, status="queued")


@router.get(
    "/v1/proposals/{proposal_id}",
    response_model=ProposalState,
)
async def get_proposal(
    proposal_id: str,
    store: Annotated[GitProposalStore, Depends(get_proposal_store)],
    _: Annotated[str, Depends(_require_api_key)],
) -> ProposalState:
    record = await store.read(proposal_id)
    if record is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    return ProposalState(
        proposal_id=record.id,
        status=record.status,
        type=record.type,
        content=record.content,
        submitted_at=record.submitted_at.isoformat(),
        submitted_by=record.submitted_by,
        target_node_id=record.target_node_id,
        evidence=list(record.evidence),
        rationale=record.rationale,
        decision_at=record.decision_at.isoformat() if record.decision_at else None,
        reason=record.reason,
        episode_uuid=record.episode_uuid,
    )
