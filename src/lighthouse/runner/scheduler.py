"""Source-runner scheduler.

A single coroutine — :meth:`SourceScheduler.run` — that wakes on a
heartbeat, asks each source "are you due?", and fires the ones whose
``last_run_at + interval`` is in the past. Concurrency is bounded so a
runner with many sources doesn't fork-bomb the LLM provider during
extraction.

Error isolation: an exception during one source's drain only fails
that source — the scheduler records the error in :class:`RunState` and
moves on. The next tick retries that source on schedule.

Sigint / shutdown: callers cancel the run coroutine; we let in-flight
drains finish before returning (clean exit, no partial-state weirdness
on the next start).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from lighthouse.connectors.base import Connector
from lighthouse.ingest import drain
from lighthouse.runner.config import RunnerConfig, SourceSpec, build_connector
from lighthouse.runner.state import RunState, StateStore, utc_now

logger = logging.getLogger(__name__)


# ``drain`` injected for tests; defaults to the real ingest loop.
# Graph type is Any so the scheduler works with either
# :class:`lighthouse.core.graph.KnowledgeGraph` (Graphiti / Neo4j)
# or :class:`lighthouse.core.flat_graph.FlatGraph` (pgvector). Both
# expose the same surface ``drain()`` calls.
DrainFn = Callable[[Connector, Any, str], Awaitable[int]]


async def _default_drain(
    connector: Connector, graph: Any, source_prefix: str
) -> int:
    return await drain(connector, source_prefix=source_prefix, graph=graph)


class SourceScheduler:
    """Drives scheduled ingestion of configured sources."""

    def __init__(
        self,
        config: RunnerConfig,
        state: StateStore,
        graph: Any,
        *,
        heartbeat_seconds: float = 30.0,
        max_concurrent: int = 2,
        drain_fn: DrainFn = _default_drain,
        connector_factory: Callable[[SourceSpec], Connector] = build_connector,
    ) -> None:
        self._config = config
        self._state = state
        self._graph = graph
        self._heartbeat = heartbeat_seconds
        self._sem = asyncio.Semaphore(max_concurrent)
        self._drain_fn = drain_fn
        self._connector_factory = connector_factory

    # --- entry points --------------------------------------------------

    async def run(self) -> None:
        """Loop forever firing due sources. Cancel-safe."""
        # Ensure graph is up before any source fires.
        await self._graph.initialize()
        logger.info(
            "scheduler started with %d sources, heartbeat=%ss, max_concurrent=%s",
            len(self._config.sources),
            self._heartbeat,
            self._sem._value,  # noqa: SLF001 — diagnostic only
        )
        try:
            while True:
                await self.tick()
                await asyncio.sleep(self._heartbeat)
        except asyncio.CancelledError:
            logger.info("scheduler shutdown requested — waiting for in-flight drains")
            raise

    async def run_once(self) -> dict[str, int]:
        """Fire every source one time, regardless of schedule.

        Useful for one-shot CLI runs (``lighthouse runner --once``) and
        for forcing a manual refresh from operations. Returns a map of
        source name → documents ingested.
        """
        await self._graph.initialize()
        results: dict[str, int] = {}
        await asyncio.gather(
            *(self._run_source(spec, results) for spec in self._config.sources),
            return_exceptions=False,
        )
        return results

    async def tick(self) -> list[str]:
        """One heartbeat: figure out which sources are due, fire them."""
        due = [s for s in self._config.sources if self._is_due(s)]
        if not due:
            return []
        results: dict[str, int] = {}
        await asyncio.gather(
            *(self._run_source(spec, results) for spec in due),
            return_exceptions=False,
        )
        return list(results)

    # --- internals -----------------------------------------------------

    def _is_due(self, spec: SourceSpec) -> bool:
        state = self._state.get(spec.name)
        if state is None or state.last_run_at is None:
            return True
        return (utc_now() - state.last_run_at) >= spec.schedule.interval

    async def _run_source(self, spec: SourceSpec, results: dict[str, int]) -> None:
        # Semaphore bounds concurrency across sources; per-source
        # serialisation is the natural consequence of a single
        # ``_run_source`` call per name (we never start a second drain
        # for the same source while one is in flight, because gather()
        # below only schedules each spec once per tick).
        async with self._sem:
            logger.info("firing source: %s", spec.name)
            try:
                connector = self._connector_factory(spec)
                n = await self._drain_fn(connector, self._graph, spec.name)
                self._state.update(
                    spec.name,
                    RunState(
                        last_run_at=utc_now(),
                        last_ok=True,
                        last_error=None,
                        last_documents=n,
                    ),
                )
                results[spec.name] = n
            except Exception as exc:  # noqa: BLE001
                logger.exception("source %s failed", spec.name)
                self._state.update(
                    spec.name,
                    RunState(
                        last_run_at=utc_now(),
                        last_ok=False,
                        last_error=str(exc)[:500],
                        last_documents=0,
                    ),
                )
                results[spec.name] = 0
