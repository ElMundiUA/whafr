"""Integration test for the SQL migration runner.

Gated like the other integration tests: skipped unless a Postgres DSN is
provided via ``LIGHTHOUSE_TEST_PG_URL`` (a throwaway pgvector DB, e.g.
``docker run --rm -e POSTGRES_PASSWORD=pw -e POSTGRES_DB=lh -p 55432:5432
pgvector/pgvector:pg16`` →
``LIGHTHOUSE_TEST_PG_URL=postgresql://postgres:pw@127.0.0.1:55432/lh``).

Verifies the baseline + workspace_id migration apply cleanly, are
idempotent on re-run, and that ``chunks.workspace_id`` defaults to
``'public'`` so the existing single-tenant corpus keeps working.
"""

from __future__ import annotations

import os

import asyncpg
import pytest

from lighthouse.core import migrator
from lighthouse.core.migrator import run_migrations

_DSN = os.environ.get("LIGHTHOUSE_TEST_PG_URL")

pytestmark = pytest.mark.skipif(
    not _DSN, reason="set LIGHTHOUSE_TEST_PG_URL to run migration integration test"
)

# Every table any migration creates — dropped for a clean slate. Extend
# when a migration adds a table (the assertion below catches forgotten
# migration FILES automatically, but not forgotten table names here).
_ALL_TABLES = (
    "webhook_deliveries",
    "webhooks",
    "importer_runs",
    "importers",
    "query_log",
    "coverage_gap_status",
    "api_keys",
    "chunks",
    "schema_migrations",
)


async def _drop_all(conn: asyncpg.Connection) -> None:
    for t in _ALL_TABLES:
        await conn.execute(f"DROP TABLE IF EXISTS {t} CASCADE")


@pytest.mark.asyncio
async def test_migrations_apply_idempotently_with_workspace_default() -> None:
    conn = await asyncpg.connect(_DSN)
    try:
        # Clean slate so the test is deterministic on a shared DB.
        await _drop_all(conn)

        applied = await run_migrations(conn, embedding_dim=1536)
        # Expected list derives from the migrations directory so this
        # test doesn't go stale every time a migration lands (it did:
        # it hardcoded 0001–0005 and broke when 0006–0009 shipped).
        expected = sorted(
            p.name for p in migrator._MIGRATIONS_DIR.glob("*.sql")
        )
        assert applied == expected
        assert applied[0] == "0001_baseline.sql"
        assert len(applied) >= 9

        col = await conn.fetchrow(
            """
            SELECT is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = 'chunks' AND column_name = 'workspace_id'
            """
        )
        assert col is not None
        assert col["is_nullable"] == "NO"
        assert "'public'" in col["column_default"]

        # 0003 makes the engine self-sufficient for importers: table +
        # tenancy column both present.
        imp_col = await conn.fetchrow(
            """
            SELECT is_nullable, column_default
            FROM information_schema.columns
            WHERE table_name = 'importers' AND column_name = 'workspace_id'
            """
        )
        assert imp_col is not None
        assert imp_col["is_nullable"] == "NO"
        assert "'public'" in imp_col["column_default"]

        # Re-run is a no-op.
        assert await run_migrations(conn, embedding_dim=1536) == []

        # A row written without workspace_id backfills to 'public'.
        await conn.execute(
            "INSERT INTO chunks (uuid, source, content, content_sha256) "
            "VALUES (gen_random_uuid(), 'test', 'hi', 'sha1')"
        )
        assert await conn.fetchval("SELECT workspace_id FROM chunks LIMIT 1") == "public"
    finally:
        await _drop_all(conn)
        await conn.close()
