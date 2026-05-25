"""Workspace scoping on the importers store (K3).

Gated like the other pg integration tests: set ``LIGHTHOUSE_TEST_PG_URL``
to a throwaway pgvector DB. Proves an importer created in one workspace
is invisible to another, and that the ownership-scoped ``get`` enforces
the tenant boundary while the unscoped ``get`` (trusted internal caller,
e.g. the run executor) still resolves the row.
"""

from __future__ import annotations

import os

import asyncpg
import pytest

from lighthouse.core.migrator import run_migrations
from lighthouse.importers import store

_DSN = os.environ.get("LIGHTHOUSE_TEST_PG_URL")

pytestmark = pytest.mark.skipif(
    not _DSN, reason="set LIGHTHOUSE_TEST_PG_URL to run importer workspace test"
)


async def _make(conn: asyncpg.Connection, name: str, workspace_id: str):
    return await store.create(
        conn,
        type_="web_pages",
        name=name,
        description=None,
        recipe="r",
        config={},
        secrets_enc=None,
        created_by=None,
        workspace_id=workspace_id,
    )


@pytest.mark.asyncio
async def test_importer_store_is_workspace_scoped() -> None:
    conn = await asyncpg.connect(_DSN)
    try:
        await run_migrations(conn, embedding_dim=8)
        await conn.execute(
            "DELETE FROM importers WHERE workspace_id IN ('ws-a', 'ws-b')"
        )

        a = await _make(conn, "A importer", "ws-a")
        b = await _make(conn, "B importer", "ws-b")
        assert a.workspace_id == "ws-a"

        # list_all is tenant-scoped.
        a_ids = {r.id for r in await store.list_all(conn, workspace_id="ws-a")}
        b_ids = {r.id for r in await store.list_all(conn, workspace_id="ws-b")}
        assert a.id in a_ids and b.id not in a_ids
        assert b.id in b_ids and a.id not in b_ids

        # Ownership-scoped get enforces the boundary.
        assert await store.get(conn, a.id, workspace_id="ws-a") is not None
        assert await store.get(conn, a.id, workspace_id="ws-b") is None
        # Unscoped get (trusted internal caller) still resolves it.
        internal = await store.get(conn, a.id)
        assert internal is not None
        assert internal.workspace_id == "ws-a"
    finally:
        await conn.execute(
            "DELETE FROM importers WHERE workspace_id IN ('ws-a', 'ws-b')"
        )
        await conn.close()
