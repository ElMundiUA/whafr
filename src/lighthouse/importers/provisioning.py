"""Per-workspace importer provisioning.

When a workspace is set up, the consumer (Ship) asks the engine to
provision a per-workspace S3 importer pointed at
``s3://<bucket>/<workspace_id>/``. The bucket + optional S3-compatible
endpoint / IAM keys are instance-level config; the prefix is derived
from the workspace id so each tenant's uploads stay in its own slice.

The operation is idempotent (get-or-create by canonical name) and
refuses the reserved ``public`` workspace.
"""

from __future__ import annotations

import asyncpg

from lighthouse.core.config import Settings
from lighthouse.core.flat_graph import PUBLIC_WORKSPACE
from lighthouse.importers import crypto, store
from lighthouse.importers.store import ImporterRow

# Canonical name → the idempotency key (unique per workspace via the
# importers_workspace_name_unique index).
WORKSPACE_S3_IMPORTER_NAME = "Workspace knowledge (S3)"


class ReservedWorkspaceError(ValueError):
    """Provisioning targeted the reserved ``public`` workspace."""


class ProvisioningConfigError(RuntimeError):
    """The engine isn't configured for per-workspace S3 ingestion."""


async def provision_workspace_s3_importer(
    conn: asyncpg.Connection, *, workspace_id: str, settings: Settings
) -> ImporterRow:
    """Get-or-create the per-workspace S3 importer. Idempotent and
    race-safe (the unique index turns a concurrent create into a
    re-fetch)."""
    if workspace_id == PUBLIC_WORKSPACE:
        raise ReservedWorkspaceError(
            f"'{PUBLIC_WORKSPACE}' is the reserved reference corpus — it "
            "can't be provisioned as a tenant workspace"
        )
    bucket = settings.lighthouse_workspace_s3_bucket
    if not bucket:
        raise ProvisioningConfigError(
            "LIGHTHOUSE_WORKSPACE_S3_BUCKET is not set — the engine can't "
            "provision per-workspace S3 importers"
        )

    existing = await store.get_by_name(
        conn, name=WORKSPACE_S3_IMPORTER_NAME, workspace_id=workspace_id
    )
    if existing is not None:
        return existing

    config: dict = {"bucket": bucket, "prefix": f"{workspace_id}/"}
    if settings.lighthouse_workspace_s3_endpoint_url:
        config["s3_endpoint_url"] = settings.lighthouse_workspace_s3_endpoint_url
    if settings.lighthouse_workspace_s3_access_id:
        config["aws_access_id"] = settings.lighthouse_workspace_s3_access_id

    secrets_enc: bytes | None = None
    if settings.lighthouse_workspace_s3_access_secret:
        try:
            secrets_enc = crypto.encrypt_secrets(
                {"aws_access_secret": settings.lighthouse_workspace_s3_access_secret}
            )
        except crypto.MissingMasterKeyError as exc:
            raise ProvisioningConfigError(str(exc)) from exc

    try:
        return await store.create(
            conn,
            type_="s3",
            name=WORKSPACE_S3_IMPORTER_NAME,
            description="Auto-provisioned per-workspace S3 source.",
            recipe="workspace-s3",
            config=config,
            secrets_enc=secrets_enc,
            created_by="provision",
            workspace_id=workspace_id,
        )
    except asyncpg.UniqueViolationError:
        # A concurrent provision won the race — return the row it wrote.
        winner = await store.get_by_name(
            conn, name=WORKSPACE_S3_IMPORTER_NAME, workspace_id=workspace_id
        )
        if winner is not None:
            return winner
        raise
