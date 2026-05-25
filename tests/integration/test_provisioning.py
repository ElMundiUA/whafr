"""Per-workspace S3 importer provisioning (K4).

Gated by ``LIGHTHOUSE_TEST_PG_URL`` (throwaway pgvector DB). Proves
provisioning is idempotent, scoped per workspace (own prefix), and
refuses the reserved ``public`` workspace / a missing bucket.
"""

from __future__ import annotations

import os

import asyncpg
import pytest

from lighthouse.core.config import Settings
from lighthouse.core.migrator import run_migrations
from lighthouse.importers import provisioning

_DSN = os.environ.get("LIGHTHOUSE_TEST_PG_URL")

pytestmark = pytest.mark.skipif(
    not _DSN, reason="set LIGHTHOUSE_TEST_PG_URL to run provisioning test"
)


@pytest.mark.asyncio
async def test_provision_s3_is_idempotent_and_scoped() -> None:
    conn = await asyncpg.connect(_DSN)
    settings = Settings(lighthouse_workspace_s3_bucket="kb-bucket")
    try:
        await run_migrations(conn, embedding_dim=8)
        await conn.execute(
            "DELETE FROM importers WHERE workspace_id IN ('ws-a', 'ws-b')"
        )

        first = await provisioning.provision_workspace_s3_importer(
            conn, workspace_id="ws-a", settings=settings
        )
        assert first.type == "s3"
        assert first.workspace_id == "ws-a"
        assert first.config["bucket"] == "kb-bucket"
        # Each tenant's uploads live under its own prefix.
        assert first.config["prefix"] == "ws-a/"

        # Idempotent: a second call returns the same row, not a dupe.
        second = await provisioning.provision_workspace_s3_importer(
            conn, workspace_id="ws-a", settings=settings
        )
        assert second.id == first.id

        # A different workspace gets a distinct importer + its own prefix.
        other = await provisioning.provision_workspace_s3_importer(
            conn, workspace_id="ws-b", settings=settings
        )
        assert other.id != first.id
        assert other.config["prefix"] == "ws-b/"

        # 'public' is reserved.
        with pytest.raises(provisioning.ReservedWorkspaceError):
            await provisioning.provision_workspace_s3_importer(
                conn, workspace_id="public", settings=settings
            )

        # No bucket configured → config error, not a half-built importer.
        with pytest.raises(provisioning.ProvisioningConfigError):
            await provisioning.provision_workspace_s3_importer(
                conn,
                workspace_id="ws-c",
                settings=Settings(lighthouse_workspace_s3_bucket=""),
            )
    finally:
        await conn.execute(
            "DELETE FROM importers WHERE workspace_id IN ('ws-a', 'ws-b')"
        )
        await conn.close()
