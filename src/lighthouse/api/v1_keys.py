"""/v1/keys — manage per-workspace API keys for the retrieval surface.

Operator-only (admin bearer). Keys are scoped to the workspace given
by ``X-Workspace`` on create; the plaintext secret (``lh_…``) is
returned exactly once — only its SHA-256 lands in the database.

Revocation is soft (``revoked_at``) so ``query_log.api_key_id``
attribution survives for billing/audit after a key dies.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lighthouse.api.dependencies import get_pg_pool, get_workspace, require_admin
from lighthouse.core.auth import generate_key

router = APIRouter(
    prefix="/v1/keys",
    tags=["v1", "keys"],
    dependencies=[Depends(require_admin)],
)


class KeyOut(BaseModel):
    id: UUID
    workspace_id: str
    name: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None


class KeyCreated(KeyOut):
    secret: str
    """Shown once — store it now, it is never retrievable again."""


class KeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


_COLUMNS = "id, workspace_id, name, scopes, created_at, last_used_at, revoked_at"


def _to_out(row: asyncpg.Record) -> KeyOut:
    return KeyOut(
        id=row["id"],
        workspace_id=row["workspace_id"],
        name=row["name"],
        scopes=list(row["scopes"] or []),
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
        revoked_at=row["revoked_at"],
    )


@router.get("/", response_model=list[KeyOut])
async def list_keys(
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
    workspace_id: Annotated[str, Depends(get_workspace)],
) -> list[KeyOut]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {_COLUMNS} FROM api_keys
             WHERE workspace_id = $1
             ORDER BY created_at DESC
            """,
            workspace_id,
        )
    return [_to_out(r) for r in rows]


@router.post("/", response_model=KeyCreated, status_code=201)
async def create_key(
    body: KeyCreate,
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
    workspace_id: Annotated[str, Depends(get_workspace)],
) -> KeyCreated:
    secret, key_hash = generate_key()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO api_keys (workspace_id, name, key_hash)
            VALUES ($1, $2, $3)
            RETURNING {_COLUMNS}
            """,
            workspace_id,
            body.name,
            key_hash,
        )
    assert row is not None
    return KeyCreated(**_to_out(row).model_dump(), secret=secret)


@router.delete("/{key_id}", response_model=KeyOut)
async def revoke_key(
    key_id: UUID,
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
    workspace_id: Annotated[str, Depends(get_workspace)],
) -> KeyOut:
    """Soft-revoke: the key stops authenticating immediately but the
    row (and query_log attribution) is kept."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE api_keys SET revoked_at = NOW()
             WHERE id = $1 AND workspace_id = $2 AND revoked_at IS NULL
            RETURNING {_COLUMNS}
            """,
            key_id,
            workspace_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="not found (or already revoked)")
    return _to_out(row)
