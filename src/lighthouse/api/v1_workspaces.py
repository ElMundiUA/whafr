"""/v1/workspaces — tenant registry + per-workspace auth policy.

Operator-only. Two jobs:

- a place to LIST tenants (previously workspaces existed only as
  scattered ``workspace_id`` strings across tables);
- the ``require_auth`` switch that closes the mixed-mode footgun: a
  flagged workspace rejects keyless retrieval even while the instance
  default (``LIGHTHOUSE_RETRIEVAL_AUTH_REQUIRED=false``) keeps e.g. a
  public corpus open.

Flag flips apply immediately in this process (cache invalidated) and
within ~30s on other replicas (TTL).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel

from lighthouse.api.dependencies import get_pg_pool, require_admin
from lighthouse.core.auth import invalidate_workspace_auth_cache

router = APIRouter(
    prefix="/v1/workspaces",
    tags=["v1", "workspaces"],
    dependencies=[Depends(require_admin)],
)

_ID = Path(min_length=1, max_length=200, pattern=r"^[a-zA-Z0-9_.\-]+$")


class WorkspaceOut(BaseModel):
    id: str
    require_auth: bool
    description: str | None
    created_at: datetime
    updated_at: datetime


class WorkspaceUpsert(BaseModel):
    require_auth: bool = False
    description: str | None = None


def _to_out(row: asyncpg.Record) -> WorkspaceOut:
    return WorkspaceOut(
        id=row["id"],
        require_auth=row["require_auth"],
        description=row["description"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get("/", response_model=list[WorkspaceOut])
async def list_workspaces(
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
) -> list[WorkspaceOut]:
    """The registry — registered workspaces with their auth policy.
    (Tenants that never registered still work; they just have no row
    here and default to the instance-wide policy.)"""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, require_auth, description, created_at, updated_at
              FROM workspaces ORDER BY id
            """
        )
    return [_to_out(r) for r in rows]


@router.put("/{workspace_id}", response_model=WorkspaceOut)
async def upsert_workspace(
    body: WorkspaceUpsert,
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
    workspace_id: Annotated[str, _ID],
) -> WorkspaceOut:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO workspaces (id, require_auth, description)
            VALUES ($1, $2, $3)
            ON CONFLICT (id) DO UPDATE
               SET require_auth = $2,
                   description = COALESCE($3, workspaces.description),
                   updated_at = NOW()
            RETURNING id, require_auth, description, created_at, updated_at
            """,
            workspace_id,
            body.require_auth,
            body.description,
        )
    assert row is not None
    invalidate_workspace_auth_cache()
    return _to_out(row)
