"""In-process proposal queue with crash-recovery.

Replaces the earlier ``asyncio.create_task(process_proposal(...))``
fire-and-forget pattern with a small managed queue:

- Bounded concurrency (semaphore) so a flood of proposals can't fork
  the Librarian into spinning every Anthropic call at once.
- Startup bootstrap: on app start, scan the store for proposals stuck
  in ``queued`` / ``evaluating`` (crashed mid-evaluation) and re-submit
  them. This is the persistence guarantee: a process restart never
  drops in-flight work, the worst case is a duplicate Librarian call.
- Shutdown drain: on app stop, wait for in-flight workers to settle
  so we don't strand a record in ``evaluating`` ourselves.

The queue is single-process. Multi-replica deployments need a
real broker (Redis/SQS/Temporal) — the seam to add it is
:meth:`ProposalQueue.submit`, which would push to the broker instead
of scheduling a local task.
"""

from __future__ import annotations

import asyncio
import logging

from lighthouse.core.graph import KnowledgeGraph
from lighthouse.librarian.agent import Librarian
from lighthouse.proposals.store import GitProposalStore
from lighthouse.proposals.worker import process_proposal

logger = logging.getLogger(__name__)


class ProposalQueue:
    """Bounded-concurrency in-process queue.

    Owns no proposal data of its own — the durable state is the file in
    :class:`GitProposalStore`. The queue only tracks which proposals
    have in-flight workers right now (so we don't double-submit) and
    bounds parallelism.
    """

    def __init__(
        self,
        *,
        store: GitProposalStore,
        librarian: Librarian,
        graph: KnowledgeGraph,
        max_concurrent: int = 4,
    ) -> None:
        self._store = store
        self._librarian = librarian
        self._graph = graph
        self._sem = asyncio.Semaphore(max_concurrent)
        self._in_flight: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    # --- lifecycle -----------------------------------------------------

    async def bootstrap(self) -> int:
        """Scan the store and re-enqueue any non-decided proposals.

        Returns the number of records picked back up. ``evaluating``
        records are re-submitted because their previous worker
        evidently didn't finish — at worst we call the Librarian
        again on the same input (idempotent in our use; the worker's
        ``status != queued`` guard catches the case where the previous
        run *did* finish and update the file).
        """
        pending = await self._store.list_pending()
        if not pending:
            return 0
        logger.info(
            "bootstrap re-enqueuing %d proposals (queued+evaluating)",
            len(pending),
        )
        for record in pending:
            # Reset ``evaluating`` to ``queued`` so the worker's
            # short-circuit at the top of process_proposal lets us
            # actually run. The store update commits the transition.
            if record.status == "evaluating":
                record.status = "queued"
                await self._store.update(record, action="requeue")
            await self.submit(record.id)
        return len(pending)

    async def drain(self) -> None:
        """Block until all in-flight workers finish.

        Called from the FastAPI lifespan ``shutdown`` hook so SIGTERM
        in a containerised deployment doesn't leave records in
        ``evaluating``.
        """
        async with self._lock:
            tasks = list(self._in_flight.values())
        if not tasks:
            return
        logger.info("draining %d in-flight proposal workers", len(tasks))
        await asyncio.gather(*tasks, return_exceptions=True)

    # --- submit --------------------------------------------------------

    async def submit(self, proposal_id: str) -> None:
        """Schedule a worker for ``proposal_id``.

        Idempotent within a single process: if a task is already
        running for this id we skip — the existing worker's update
        will land the final state. Cross-process idempotency is the
        worker's responsibility (it short-circuits on
        ``status != queued``).
        """
        async with self._lock:
            existing = self._in_flight.get(proposal_id)
            if existing is not None and not existing.done():
                logger.debug("proposal %s already in flight — skipping", proposal_id)
                return
            task = asyncio.create_task(
                self._run(proposal_id),
                name=f"proposal-{proposal_id[:8]}",
            )
            self._in_flight[proposal_id] = task

    async def _run(self, proposal_id: str) -> None:
        try:
            async with self._sem:
                await process_proposal(
                    proposal_id,
                    store=self._store,
                    librarian=self._librarian,
                    graph=self._graph,
                )
        finally:
            async with self._lock:
                self._in_flight.pop(proposal_id, None)
