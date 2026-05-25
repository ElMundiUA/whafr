"""Tiny forward-only SQL migration runner for the flat (pgvector) backend.

Why not Alembic: the engine is raw ``asyncpg`` with no ORM and exactly
one dynamic value in the DDL (the embedding dimension), so a numbered-
``.sql`` runner with a single-token substitution is lighter and matches
the codebase than an Alembic + SQLAlchemy setup.

Contract
--------
- Migrations live in ``migrations/NNNN_*.sql`` and are applied in
  filename (lexical) order. Number them zero-padded.
- Each file may contain multiple statements; it runs as one batch.
- ``__EMBEDDING_DIM__`` is substituted with the configured embedding
  dimension before execution (the only templated value).
- Applied versions are recorded in ``schema_migrations``. The whole run
  happens inside a single transaction guarded by a
  ``pg_advisory_xact_lock`` so two booting workers can't race, and a
  failure rolls the batch back (nothing recorded → retried next boot).
- Files MUST be idempotent-safe (``IF NOT EXISTS``) so the ``0001``
  baseline is a no-op on an already-provisioned prod DB.
"""

from __future__ import annotations

import logging
from pathlib import Path

import asyncpg

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_EMBEDDING_DIM_TOKEN = "__EMBEDDING_DIM__"
# Arbitrary fixed key so concurrent boots serialize on the same lock.
_ADVISORY_LOCK_KEY = 0x6C68_6D67  # "lhmg"


async def run_migrations(
    conn: asyncpg.Connection, *, embedding_dim: int
) -> list[str]:
    """Apply any unapplied ``.sql`` migrations in order. Returns the list
    of versions newly applied (empty when the DB is already current)."""
    newly: list[str] = []
    async with conn.transaction():
        # Serialize concurrent boots — only one worker migrates at a time;
        # the others block here then see every version already applied.
        await conn.execute(
            "SELECT pg_advisory_xact_lock($1)", _ADVISORY_LOCK_KEY
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        applied = {
            row["version"]
            for row in await conn.fetch("SELECT version FROM schema_migrations")
        }
        for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            version = path.name
            if version in applied:
                continue
            sql = path.read_text(encoding="utf-8").replace(
                _EMBEDDING_DIM_TOKEN, str(int(embedding_dim))
            )
            await conn.execute(sql)
            await conn.execute(
                "INSERT INTO schema_migrations (version) VALUES ($1)", version
            )
            newly.append(version)
            logger.info("migration applied: %s", version)
    return newly
