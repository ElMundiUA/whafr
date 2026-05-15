#!/usr/bin/env python3
"""Retroactive graph cleanup.

Walks the existing Neo4j graph and drops edges that fail the same
quality gate the search-time filter applies. Lets us scrub low-quality
facts from already-ingested corpus without re-ingesting from sources.

Rules (must match :class:`KnowledgeGraph.search` post-filter):

- summary (the edge's ``fact``) length < ``MIN_SUMMARY_CHARS``
- duplicate normalized first-80-chars (keep oldest)
- starts with junk prefix (e.g. cookie banner residue)

Usage::

    python tools/clean_graph.py --dry-run        # report what would go
    python tools/clean_graph.py --apply          # actually delete

Run after large ingests, before sharing a snapshot externally, etc.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lighthouse.core.config import get_settings  # noqa: E402
from lighthouse.core.graph import KnowledgeGraph  # noqa: E402

logger = logging.getLogger("clean_graph")

MIN_SUMMARY_CHARS = 40
JUNK_PREFIXES = (
    "this site uses",
    "cookie",
    "privacy policy",
    "skip to main content",
    "skip to content",
    "this page is",
    "404",
    "not found",
    "loading",
    "page not found",
    "an error occurred",
)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry-run)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(message)s",
    )

    settings = get_settings()
    from neo4j import AsyncGraphDatabase

    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password),
    )

    short = 0
    junk = 0
    dupes = 0
    seen_keys: set[str] = set()
    to_delete: list[str] = []

    records: list[tuple[str, str]] = []
    async with driver.session(database=settings.neo4j_database) as session:
        result = await session.run(
            "MATCH ()-[e:RELATES_TO]->() "
            "RETURN e.uuid AS uuid, e.fact AS fact LIMIT 200000"
        )
        async for row in result:
            records.append((str(row["uuid"] or ""), str(row["fact"] or "")))

    for uuid, fact in records:
            fact = fact.strip()
            if not uuid:
                continue
            if len(fact) < MIN_SUMMARY_CHARS:
                short += 1
                to_delete.append(uuid)
                continue
            low = fact.lower()
            if any(low.startswith(p) for p in JUNK_PREFIXES):
                junk += 1
                to_delete.append(uuid)
                continue
            key = " ".join(low.split())[:80]
            if key in seen_keys:
                dupes += 1
                to_delete.append(uuid)
                continue
            seen_keys.add(key)

    total = short + junk + dupes
    print(f"scanned {len(records):>5}  edges")
    print(f"  short  (<{MIN_SUMMARY_CHARS} chars):  {short:>5}")
    print(f"  junk   (banner/error prefix): {junk:>5}")
    print(f"  dupes  (normalized 80c):      {dupes:>5}")
    print(f"  TOTAL to delete:              {total:>5}")

    if not args.apply:
        print("\n(dry-run; pass --apply to delete)")
        await driver.close()
        return 0

    if not to_delete:
        print("nothing to delete")
        await driver.close()
        return 0

    BATCH = 500
    async with driver.session(database=settings.neo4j_database) as session:
        for i in range(0, len(to_delete), BATCH):
            batch = to_delete[i : i + BATCH]
            await session.run(
                "UNWIND $uuids AS u "
                "MATCH ()-[e:RELATES_TO {uuid: u}]->() DELETE e",
                uuids=batch,
            )
            print(f"  deleted batch {i // BATCH + 1}/{(len(to_delete) - 1) // BATCH + 1}")

    print(f"\ndeleted {len(to_delete)} edges")
    await driver.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
