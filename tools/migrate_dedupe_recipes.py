"""One-shot migration: collapse same-content rows ingested under
different recipe slugs into one row carrying multi-recipe
membership.

Schema assumptions (after FlatGraph.initialize on latest code):
    chunks.source           — currently "<recipe>:<canonical>" but
                              becomes just "<canonical>" after this
                              script. Going forward the ingest writes
                              the canonical form directly.
    chunks.recipes  TEXT[]  — multi-recipe membership. Was added by
                              the schema-evolve; default empty array.

Steps the script does, in a single transaction:

  1. CREATE TEMP TABLE with (uuid, recipe, canonical_source, new_uuid)
     derived per row. The recipe is the segment before the first ':',
     canonical is the rest. ``new_uuid`` is the deterministic uuid
     the ingest path WOULD pick if it wrote the canonical form
     today: uuid5(NAMESPACE_URL, canonical || full_hash || chunk_index).
  2. For each group of rows sharing (new_uuid), pick a survivor
     (min(uuid)), aggregate recipes from all rows, UPDATE the survivor
     to rewrite source/recipes/uuid in one pass, and DELETE the rest.

The deterministic uuid match means subsequent ingest runs hit ON
CONFLICT cleanly — no orphaned rows.

Run with:
    LIGHTHOUSE_PG_URL=...
    uv run python tools/migrate_dedupe_recipes.py
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import uuid
from collections import defaultdict

import asyncpg

logger = logging.getLogger("migrate_dedupe_recipes")
logging.basicConfig(level=logging.INFO, format="%(message)s")

_NAMESPACE = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")


def deterministic_uuid(source: str, full_body_sha256: str, chunk_index: int) -> str:
    """Mirrors FlatGraph._deterministic_uuid — same UUID the ingest
    path produces for a canonical-source write."""
    seed = f"{source}|{full_body_sha256}|{chunk_index}"
    return str(uuid.uuid5(_NAMESPACE, seed))


async def main() -> None:
    dsn = os.environ["LIGHTHOUSE_PG_URL"]
    conn = await asyncpg.connect(dsn, statement_cache_size=0)

    # Guard: recipes column must already exist (added by schema-evolve).
    has_recipes = await conn.fetchval(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_name='chunks' AND column_name='recipes'"
    )
    if not has_recipes:
        logger.error("chunks.recipes column missing; deploy latest "
                     "FlatGraph schema before running this migration")
        return

    rows = await conn.fetch(
        "SELECT uuid::text, source, full_body_sha256, chunk_index, recipes "
        "  FROM chunks"
    )
    logger.info("loaded %d rows", len(rows))

    # Group by canonical (new_uuid). Build a survivor map +
    # per-survivor recipes superset.
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        src = r["source"] or ""
        idx = src.find(":")
        if idx < 0:
            recipe = ""
            canonical = src
        else:
            recipe = src[:idx]
            canonical = src[idx + 1:]
        new_uuid = deterministic_uuid(
            canonical, r["full_body_sha256"] or "", r["chunk_index"] or 0
        )
        groups[new_uuid].append({
            "old_uuid": r["uuid"],
            "canonical": canonical,
            "recipe": recipe,
            "existing_recipes": list(r["recipes"] or []),
        })

    survivors: list[tuple[str, str, str, list[str]]] = []  # new_uuid, old_uuid, canonical, recipes
    to_delete: list[str] = []
    multi_recipe_dups = 0
    for new_uuid, members in groups.items():
        # Survivor = smallest old uuid (deterministic, idempotent).
        members.sort(key=lambda m: m["old_uuid"])
        survivor = members[0]
        recipes_union = sorted({
            r for m in members
            for r in (m["existing_recipes"] + ([m["recipe"]] if m["recipe"] else []))
        })
        survivors.append(
            (new_uuid, survivor["old_uuid"], survivor["canonical"], recipes_union)
        )
        if len(members) > 1:
            multi_recipe_dups += 1
            for m in members[1:]:
                to_delete.append(m["old_uuid"])

    logger.info(
        "groups: %d (of which %d shared across multiple recipes)",
        len(survivors), multi_recipe_dups,
    )
    logger.info("to-delete: %d", len(to_delete))

    # Transactional rewrite. We update the survivor's uuid in two
    # phases to avoid uniqueness clashes when survivor's new_uuid
    # equals another row's old_uuid in the same batch: first delete
    # losers, then update survivors.
    async with conn.transaction():
        # Phase 1: delete the losers.
        if to_delete:
            for batch_start in range(0, len(to_delete), 500):
                batch = to_delete[batch_start:batch_start + 500]
                await conn.execute(
                    "DELETE FROM chunks WHERE uuid::text = ANY($1::text[])",
                    batch,
                )
            logger.info("deleted %d duplicate rows", len(to_delete))

        # Phase 2: rewrite survivors. uuid change requires the new
        # value not already exist — but losers are gone.
        for batch_start in range(0, len(survivors), 500):
            batch = survivors[batch_start:batch_start + 500]
            await conn.executemany(
                """
                UPDATE chunks
                   SET source = $3,
                       recipes = $4,
                       uuid = $1::uuid
                 WHERE uuid::text = $2
                """,
                batch,
            )
        logger.info("rewrote %d survivor rows", len(survivors))

    remaining = await conn.fetchval("SELECT COUNT(*) FROM chunks")
    logger.info("chunks remaining: %s", f"{remaining:,}")
    await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
