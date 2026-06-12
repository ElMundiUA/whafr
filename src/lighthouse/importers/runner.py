"""End-to-end importer execution.

`run_importer(importer_id)` loads the row, decrypts secrets, asks
the registry for the type's class, builds a Connector, hands it to
`ingest.drain()`. Status / errors / counters are persisted to the
`importers` + `importer_runs` tables before, during, and after.

Concurrency: a single importer can have at most one in-flight run.
The DB-side guard is the `status='running'` column on `importers`;
the runner sets it via UPDATE … WHERE status IN ('idle','error') so
a second click during a long crawl is a no-op. The DB enforces a
single source of truth; in-process locks are unnecessary.
"""

from __future__ import annotations

import asyncio
import logging
import traceback
from uuid import UUID

import asyncpg

from lighthouse.core.metrics import IMPORTER_RUNS
from lighthouse.importers import crypto, store
from lighthouse.importers.registry import lookup_importer
from lighthouse.ingest import drain
from lighthouse.webhooks import emit_event

logger = logging.getLogger(__name__)


class RunnerError(RuntimeError):
    """Raised when a run can't even be started (importer missing,
    type unregistered, status already running, etc.)."""


async def try_claim(
    conn: asyncpg.Connection, importer_id: UUID
) -> bool:
    """Atomically flip status idle/error/queued → running. Returns
    False if the importer is already running or doesn't exist."""
    row = await conn.fetchrow(
        """
        UPDATE importers
           SET status = 'running', last_error = NULL, updated_at = NOW()
         WHERE id = $1 AND status IN ('idle', 'error', 'queued')
        RETURNING id
        """,
        importer_id,
    )
    return row is not None


async def run_importer(
    pool: asyncpg.Pool,
    importer_id: UUID,
    *,
    triggered_by: str | None,
    run_id: UUID | None = None,
) -> UUID:
    """Run an importer end-to-end. Returns the `importer_runs.id` row
    written for the run. Re-raises on hard errors after persisting
    the failure to the run row.

    ``run_id``: an already-claimed queued run to execute (the worker
    path). Omitted → a fresh 'running' row is created (direct callers:
    the ``run-importers`` cron command and the scheduler)."""
    async with pool.acquire() as conn:
        claimed = await try_claim(conn, importer_id)
        if not claimed:
            existing = await store.get(conn, importer_id)
            if existing is None:
                raise RunnerError(f"Importer {importer_id} not found")
            raise RunnerError(
                f"Importer {importer_id} is already {existing.status}"
            )
        row = await store.get(conn, importer_id)
        assert row is not None
        if run_id is None:
            run_id = await store.start_run(
                conn, importer_id, triggered_by=triggered_by
            )
    await emit_event(
        pool,
        "importer.run.started",
        {
            "importer_id": str(importer_id),
            "importer_name": row.name,
            "importer_type": row.type,
            "run_id": str(run_id),
            "triggered_by": triggered_by,
        },
        workspace_id=row.workspace_id,
    )

    # Decrypt + build connector outside the txn — these can be slow
    # and we don't want to hold a pool connection through a long crawl.
    try:
        cls = lookup_importer(row.type)
    except KeyError as exc:
        await _persist_failure(
            pool, importer_id, run_id, str(exc),
            workspace_id=row.workspace_id,
        )
        raise RunnerError(str(exc)) from exc

    try:
        secrets = crypto.decrypt_secrets(row.secrets_enc)
    except crypto.MissingMasterKeyError as exc:
        if row.secrets_enc:
            await _persist_failure(
                pool, importer_id, run_id, str(exc),
                workspace_id=row.workspace_id,
            )
            raise
        secrets = {}
    except crypto.SecretsCorruptError as exc:
        await _persist_failure(
            pool, importer_id, run_id, str(exc),
            workspace_id=row.workspace_id,
        )
        raise

    importer = cls()
    try:
        connector = importer.build_connector(row.config, secrets)
    except Exception as exc:  # adapter bug or bad config
        msg = f"build_connector failed: {exc}\n{traceback.format_exc()}"
        await _persist_failure(pool, importer_id, run_id, msg, workspace_id=row.workspace_id)
        raise

    # Drain — the bulk of the work happens here. We don't have item-
    # count counters yet (drain returns chunk count), so items_total
    # stays None and chunks_added carries the work signal.
    try:
        # Stamp every chunk with the importer's workspace so a
        # per-workspace importer's output lands in that tenant's slice.
        n_chunks = await drain(
            connector, source_prefix=row.recipe, workspace_id=row.workspace_id
        )
    except Exception as exc:
        msg = f"drain failed: {exc}\n{traceback.format_exc()}"
        await _persist_failure(pool, importer_id, run_id, msg, workspace_id=row.workspace_id)
        raise

    async with pool.acquire() as conn:
        await store.finish_run(
            conn,
            run_id,
            status="success",
            items_total=None,
            items_done=n_chunks,
            chunks_added=n_chunks,
            error_text=None,
        )
        await store.set_status(
            conn, importer_id, status="idle", last_error=None, bump_last_run=True
        )
    IMPORTER_RUNS.labels(status="success").inc()
    await emit_event(
        pool,
        "importer.run.finished",
        {
            "importer_id": str(importer_id),
            "importer_name": row.name,
            "importer_type": row.type,
            "run_id": str(run_id),
            "status": "success",
            "chunks_added": n_chunks,
        },
        workspace_id=row.workspace_id,
    )
    return run_id


async def _persist_failure(
    pool: asyncpg.Pool,
    importer_id: UUID,
    run_id: UUID,
    error_text: str,
    *,
    workspace_id: str = "public",
) -> None:
    """Best-effort: persist a runner failure to the run row + flip the
    importer back to 'error', emit importer.run.finished with
    status=error. Swallows nested errors so the original exception
    propagates."""
    try:
        async with pool.acquire() as conn:
            await store.finish_run(
                conn,
                run_id,
                status="error",
                items_total=None,
                items_done=0,
                chunks_added=0,
                error_text=error_text[:8000],
            )
            await store.set_status(
                conn,
                importer_id,
                status="error",
                last_error=error_text[:8000],
                bump_last_run=True,
            )
        IMPORTER_RUNS.labels(status="error").inc()
        await emit_event(
            pool,
            "importer.run.finished",
            {
                "importer_id": str(importer_id),
                "run_id": str(run_id),
                "status": "error",
                "error": error_text[:1000],
            },
            workspace_id=workspace_id,
        )
    except Exception:
        logger.exception("Failed to persist importer-run failure")


# ────────────────────────── Run-queue worker ──────────────────────────


async def process_one_queued(pool: asyncpg.Pool) -> bool:
    """Claim and execute one queued run. Returns False when the queue
    is empty. Failures are persisted to the run row by run_importer —
    the worker itself never raises for a failed run."""
    async with pool.acquire() as conn, conn.transaction():
        claimed = await store.claim_queued_run(conn)
    if claimed is None:
        return False
    run_id, importer_id = claimed
    try:
        await run_importer(
            pool, importer_id, triggered_by="queue", run_id=run_id
        )
    except RunnerError as exc:
        # Importer raced into a non-claimable state (e.g. concurrent
        # direct run). The run row would dangle as 'running' — close it.
        logger.warning("queued run %s not runnable: %s", run_id, exc)
        async with pool.acquire() as conn:
            await store.finish_run(
                conn, run_id, status="cancelled", items_total=None,
                items_done=0, chunks_added=0, error_text=str(exc)[:8000],
            )
    except Exception:
        logger.exception("queued run %s failed", run_id)
    return True


async def run_queue_worker(
    pool: asyncpg.Pool, *, poll_interval: float = 3.0
) -> None:
    """Long-lived task draining the importer-run queue.

    One run at a time per process — crawls are heavy; horizontal scale
    comes from replicas (SKIP LOCKED keeps them from colliding).
    Started from the API lifespan; cancelled on shutdown. A run in
    flight during SIGKILL is re-queued by the boot sweep."""
    logger.info("importer run-queue worker started (poll=%ss)", poll_interval)
    while True:
        try:
            if await process_one_queued(pool):
                continue  # drain back-to-back while the queue has work
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("run-queue worker tick failed")
        await asyncio.sleep(poll_interval)
