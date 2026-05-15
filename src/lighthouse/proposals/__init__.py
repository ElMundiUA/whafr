"""Proposal pipeline: store + worker.

Proposals submitted to ``POST /v1/propose`` land in a git-backed store
(one markdown file per proposal) and are evaluated asynchronously by
the Librarian agent. Accepted proposals become episodes in the
knowledge graph; rejected ones surface their reason back to the
submitter via ``GET /v1/proposals/:id``.
"""

from lighthouse.proposals.queue import ProposalQueue
from lighthouse.proposals.store import (
    GitProposalStore,
    ProposalRecord,
    ProposalStatus,
)
from lighthouse.proposals.worker import process_proposal

__all__ = [
    "GitProposalStore",
    "ProposalQueue",
    "ProposalRecord",
    "ProposalStatus",
    "process_proposal",
]
