"""CRUD against the `importers` + `importer_runs` tables.

Thin async wrappers — no business logic. The runner handles
encrypt/decrypt and the schema validation; this module only moves
bytes in and out of Postgres.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import asyncpg


@dataclass(slots=True)
class ImporterRow:
    id: UUID
    type: str
    name: str
    description: str | None
    recipe: str
    config: dict[str, Any]
    secrets_enc: bytes | None
    enabled: bool
    status: str
    last_run_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class RunRow:
    id: UUID
    importer_id: UUID
    started_at: datetime
    finished_at: datetime | None
    status: str
    items_total: int | None
    items_done: int
    chunks_added: int
    error_text: str | None
    triggered_by: str | None


def _row_to_importer(row: asyncpg.Record) -> ImporterRow:
    raw_cfg = row["config"]
    cfg = json.loads(raw_cfg) if isinstance(raw_cfg, str) else (raw_cfg or {})
    return ImporterRow(
        id=row["id"],
        type=row["type"],
        name=row["name"],
        description=row["description"],
        recipe=row["recipe"],
        config=cfg,
        secrets_enc=(bytes(row["secrets_enc"]) if row["secrets_enc"] else None),
        enabled=row["enabled"],
        status=row["status"],
        last_run_at=row["last_run_at"],
        last_error=row["last_error"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_to_run(row: asyncpg.Record) -> RunRow:
    return RunRow(
        id=row["id"],
        importer_id=row["importer_id"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        status=row["status"],
        items_total=row["items_total"],
        items_done=row["items_done"],
        chunks_added=row["chunks_added"],
        error_text=row["error_text"],
        triggered_by=row["triggered_by"],
    )


# ──────────────────────────── Importers ────────────────────────────


async def list_all(conn: asyncpg.Connection) -> list[ImporterRow]:
    rows = await conn.fetch(
        """
        SELECT id, type, name, description, recipe, config, secrets_enc,
               enabled, status, last_run_at, last_error, created_at, updated_at
        FROM importers
        ORDER BY updated_at DESC
        """
    )
    return [_row_to_importer(r) for r in rows]


async def get(conn: asyncpg.Connection, importer_id: UUID) -> ImporterRow | None:
    row = await conn.fetchrow(
        """
        SELECT id, type, name, description, recipe, config, secrets_enc,
               enabled, status, last_run_at, last_error, created_at, updated_at
        FROM importers WHERE id = $1
        """,
        importer_id,
    )
    return _row_to_importer(row) if row else None


async def create(
    conn: asyncpg.Connection,
    *,
    type_: str,
    name: str,
    description: str | None,
    recipe: str,
    config: dict[str, Any],
    secrets_enc: bytes | None,
    created_by: str | None,
) -> ImporterRow:
    row = await conn.fetchrow(
        """
        INSERT INTO importers
          (type, name, description, recipe, config, secrets_enc, created_by)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
        RETURNING id, type, name, description, recipe, config, secrets_enc,
                  enabled, status, last_run_at, last_error, created_at, updated_at
        """,
        type_,
        name,
        description,
        recipe,
        json.dumps(config),
        secrets_enc,
        created_by,
    )
    assert row is not None
    return _row_to_importer(row)


async def update(
    conn: asyncpg.Connection,
    importer_id: UUID,
    *,
    name: str | None = None,
    description: str | None = None,
    recipe: str | None = None,
    config: dict[str, Any] | None = None,
    secrets_enc: bytes | None = None,
    keep_secrets: bool = True,
    enabled: bool | None = None,
) -> ImporterRow | None:
    """Patch-style update. `keep_secrets=True` leaves the existing
    `secrets_enc` blob untouched (no overwrite with NULL) — pass
    `False` together with `secrets_enc=None` to clear it."""
    sets: list[str] = []
    args: list[Any] = []
    if name is not None:
        sets.append(f"name = ${len(args) + 1}")
        args.append(name)
    if description is not None:
        sets.append(f"description = ${len(args) + 1}")
        args.append(description)
    if recipe is not None:
        sets.append(f"recipe = ${len(args) + 1}")
        args.append(recipe)
    if config is not None:
        sets.append(f"config = ${len(args) + 1}::jsonb")
        args.append(json.dumps(config))
    if not keep_secrets:
        sets.append(f"secrets_enc = ${len(args) + 1}")
        args.append(secrets_enc)
    if enabled is not None:
        sets.append(f"enabled = ${len(args) + 1}")
        args.append(enabled)
    if not sets:
        return await get(conn, importer_id)
    sets.append("updated_at = NOW()")
    args.append(importer_id)
    row = await conn.fetchrow(
        f"""
        UPDATE importers SET {", ".join(sets)}
        WHERE id = ${len(args)}
        RETURNING id, type, name, description, recipe, config, secrets_enc,
                  enabled, status, last_run_at, last_error, created_at, updated_at
        """,
        *args,
    )
    return _row_to_importer(row) if row else None


async def delete(conn: asyncpg.Connection, importer_id: UUID) -> bool:
    r = await conn.execute("DELETE FROM importers WHERE id = $1", importer_id)
    return r.endswith(" 1")


async def set_status(
    conn: asyncpg.Connection,
    importer_id: UUID,
    *,
    status: str,
    last_error: str | None = None,
    bump_last_run: bool = False,
) -> None:
    if bump_last_run:
        await conn.execute(
            """
            UPDATE importers
               SET status = $1, last_error = $2, last_run_at = NOW(), updated_at = NOW()
             WHERE id = $3
            """,
            status,
            last_error,
            importer_id,
        )
    else:
        await conn.execute(
            """
            UPDATE importers SET status = $1, last_error = $2, updated_at = NOW()
             WHERE id = $3
            """,
            status,
            last_error,
            importer_id,
        )


# ─────────────────────────── Run history ───────────────────────────


async def start_run(
    conn: asyncpg.Connection,
    importer_id: UUID,
    *,
    triggered_by: str | None,
) -> UUID:
    row = await conn.fetchrow(
        """
        INSERT INTO importer_runs (importer_id, status, triggered_by)
        VALUES ($1, 'running', $2)
        RETURNING id
        """,
        importer_id,
        triggered_by,
    )
    assert row is not None
    return row["id"]


async def finish_run(
    conn: asyncpg.Connection,
    run_id: UUID,
    *,
    status: str,
    items_total: int | None,
    items_done: int,
    chunks_added: int,
    error_text: str | None,
) -> None:
    await conn.execute(
        """
        UPDATE importer_runs
           SET status = $1, finished_at = NOW(),
               items_total = $2, items_done = $3,
               chunks_added = $4, error_text = $5
         WHERE id = $6
        """,
        status,
        items_total,
        items_done,
        chunks_added,
        error_text,
        run_id,
    )


async def sweep_orphans(conn: asyncpg.Connection) -> int:
    """Mark any importer / importer_run still in ``running`` as
    cancelled. Called on API startup so a pod kill mid-crawl doesn't
    leave the importer perma-stuck.

    Returns the number of runs flipped — useful to log."""
    n = await conn.fetchval(
        """
        WITH stuck AS (
            UPDATE importer_runs
               SET status = 'cancelled',
                   finished_at = NOW(),
                   error_text = COALESCE(error_text, 'pod restart')
             WHERE status = 'running'
            RETURNING id
        )
        SELECT count(*) FROM stuck
        """,
    )
    await conn.execute(
        """
        UPDATE importers
           SET status = 'idle',
               last_error = COALESCE(last_error, 'pod restart while running'),
               updated_at = NOW()
         WHERE status IN ('running', 'queued')
        """,
    )
    return int(n or 0)


async def recent_runs(
    conn: asyncpg.Connection,
    importer_id: UUID,
    *,
    limit: int = 20,
) -> list[RunRow]:
    rows = await conn.fetch(
        """
        SELECT id, importer_id, started_at, finished_at, status,
               items_total, items_done, chunks_added, error_text, triggered_by
        FROM importer_runs
        WHERE importer_id = $1
        ORDER BY started_at DESC
        LIMIT $2
        """,
        importer_id,
        limit,
    )
    return [_row_to_run(r) for r in rows]
