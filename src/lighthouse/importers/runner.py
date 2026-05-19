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

import logging
import traceback
from uuid import UUID

import asyncpg

from lighthouse.importers import crypto, store
from lighthouse.importers.registry import lookup_importer
from lighthouse.ingest import drain

logger = logging.getLogger(__name__)


class RunnerError(RuntimeError):
    """Raised when a run can't even be started (importer missing,
    type unregistered, status already running, etc.)."""


async def try_claim(
    conn: asyncpg.Connection, importer_id: UUID
) -> bool:
    """Atomically flip status idle/error → running. Returns False if
    the importer is already running or doesn't exist."""
    row = await conn.fetchrow(
        """
        UPDATE importers
           SET status = 'running', last_error = NULL, updated_at = NOW()
         WHERE id = $1 AND status IN ('idle', 'error')
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
) -> UUID:
    """Run an importer end-to-end. Returns the `importer_runs.id` row
    written for the run. Re-raises on hard errors after persisting
    the failure to the run row."""
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
        run_id = await store.start_run(
            conn, importer_id, triggered_by=triggered_by
        )

    # Decrypt + build connector outside the txn — these can be slow
    # and we don't want to hold a pool connection through a long crawl.
    try:
        cls = lookup_importer(row.type)
    except KeyError as exc:
        await _persist_failure(pool, importer_id, run_id, str(exc))
        raise RunnerError(str(exc)) from exc

    try:
        secrets = crypto.decrypt_secrets(row.secrets_enc)
    except crypto.MissingMasterKeyError as exc:
        if row.secrets_enc:
            await _persist_failure(pool, importer_id, run_id, str(exc))
            raise
        secrets = {}
    except crypto.SecretsCorruptError as exc:
        await _persist_failure(pool, importer_id, run_id, str(exc))
        raise

    importer = cls()
    try:
        connector = importer.build_connector(row.config, secrets)
    except Exception as exc:  # adapter bug or bad config
        msg = f"build_connector failed: {exc}\n{traceback.format_exc()}"
        await _persist_failure(pool, importer_id, run_id, msg)
        raise

    # Drain — the bulk of the work happens here. We don't have item-
    # count counters yet (drain returns chunk count), so items_total
    # stays None and chunks_added carries the work signal.
    try:
        n_chunks = await drain(connector, source_prefix=row.recipe)
    except Exception as exc:
        msg = f"drain failed: {exc}\n{traceback.format_exc()}"
        await _persist_failure(pool, importer_id, run_id, msg)
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
    return run_id


async def _persist_failure(
    pool: asyncpg.Pool,
    importer_id: UUID,
    run_id: UUID,
    error_text: str,
) -> None:
    """Best-effort: persist a runner failure to the run row + flip the
    importer back to 'error'. Swallows nested errors so the original
    exception propagates."""
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
    except Exception:
        logger.exception("Failed to persist importer-run failure")
