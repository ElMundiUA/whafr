"""Proposal pipeline endpoint.

Anyone with the shared API key can submit a structured proposal — a new
fact, a correction to an existing one, a deprecation. The librarian agent
evaluates it asynchronously; this endpoint just accepts and queues.

There is no tenant model. The API key is binary: have it, you can propose;
don't, you can't. To track "who proposed what", clients populate
``submitted_by`` themselves — it's stored verbatim, never validated.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from lighthouse.core.config import get_settings

router = APIRouter(tags=["proposal"])

# Header is conventional but configurable: X-Lighthouse-Key. Using a
# distinct header (vs Authorization Bearer) makes it obvious in logs
# that this is a Lighthouse-specific credential, not a general bearer.
_api_key_header = APIKeyHeader(name="X-Lighthouse-Key", auto_error=False)


def _require_api_key(key: Annotated[str | None, Depends(_api_key_header)]) -> str:
    expected = get_settings().lighthouse_proposal_api_key
    if not expected:
        # Empty configured key means "auth disabled" — useful for local dev,
        # explicit rather than implicit so production misconfig is loud.
        return "anonymous"
    if not key or key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing X-Lighthouse-Key",
        )
    return key


class Proposal(BaseModel):
    """Wire format for a proposed change.

    Kept deliberately flat — clients shouldn't have to learn a graph schema
    to submit. The librarian extracts entities, decides on dedup, and writes
    the actual graph nodes.
    """

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
    status: Literal["queued", "rejected"] = "queued"


@router.post(
    "/v1/propose",
    response_model=ProposalReceipt,
    status_code=status.HTTP_202_ACCEPTED,
)
async def propose(
    proposal: Proposal,
    _: Annotated[str, Depends(_require_api_key)],
) -> ProposalReceipt:
    # Stub — wires to librarian queue in a later phase.
    import uuid

    return ProposalReceipt(proposal_id=str(uuid.uuid4()), status="queued")
