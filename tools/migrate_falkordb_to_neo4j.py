"""One-off migration: FalkorDB graph → Neo4j 5.26.

Reads all Episodic / Entity nodes and RELATES_TO / MENTIONS edges out
of a FalkorDB instance, then recreates them inside Neo4j with their
embeddings intact so Graphiti hybrid search keeps working without
re-ingest.

Usage::

    FALKORDB_HOST=localhost FALKORDB_PORT=6379 FALKORDB_DATABASE=lighthouse \\
    NEO4J_URI=bolt://localhost:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=... \\
    python tools/migrate_falkordb_to_neo4j.py --apply

Pass ``--dry-run`` (default) to count what would move without writing.

We rely on Graphiti's existing labels (Entity, Episodic) and edge types
(RELATES_TO, MENTIONS) so the destination is wire-compatible with the
same Graphiti version we run in production. Vector + fulltext indexes
are NOT recreated here — call ``KnowledgeGraph.initialize()`` once after
the migration to rebuild them.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

import redis.asyncio as redis_async
from neo4j import AsyncGraphDatabase


# ---------- FalkorDB read helpers -----------------------------------------

# FalkorDB returns rows in a compact format: each value is
# ``[type_code, value]`` where type_code is an int. We only care about
# scalar types (string / int / float / array of floats) for our schema.

def _decode_cell(cell: Any) -> Any:
    """Decode one FalkorDB compact cell."""
    if not isinstance(cell, list) or len(cell) < 2:
        return cell
    type_code, value = cell[0], cell[1]
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, list):
        # Array — recurse. Arrays of floats are returned as bytes-list
        # per element; arrays of arrays appear for label lists.
        return [_decode_cell(v) if isinstance(v, list) else v for v in value]
    return value


def _decode_props(prop_list: Any) -> dict[str, Any]:
    """Decode a FalkorDB ``properties(n)`` cell into a Python dict."""
    inner = prop_list[1] if isinstance(prop_list, list) and len(prop_list) >= 2 else prop_list
    out: dict[str, Any] = {}
    for prop in inner or []:
        # Each prop is [key, type_code, value] in compact mode.
        if not isinstance(prop, list) or len(prop) < 2:
            continue
        # FalkorDB compact format for a property:
        # [<keyId int>, <type_code>, <value>]. The key is referenced
        # by index into a separate header. The PROPERTIES() call in
        # non-compact mode returns ``{key: value}`` directly — easier.
        # We switch to non-compact for the property read.
        continue  # placeholder — see _query_uncompact below.
    return out


def _decode_compact_cell(cell: Any) -> Any:
    """Decode one ``--compact`` cell.

    FalkorDB compact cells are ``[type_code, value]``. We handle the
    types our schema uses; everything unknown comes back as the raw
    value so the caller can inspect it.
    """
    if cell is None:
        return None
    if not isinstance(cell, list) or len(cell) < 2:
        # already a scalar
        return cell
    type_code, value = cell[0], cell[1]
    if value is None:
        return None
    # 1 = NULL
    if type_code == 1:
        return None
    # 2 = STRING (bytes)
    if type_code == 2:
        return value.decode("utf-8", errors="replace") if isinstance(value, (bytes, bytearray)) else str(value)
    # 3 = INTEGER, 4 = BOOLEAN, 5 = DOUBLE
    if type_code in (3, 4, 5):
        return value
    # 6 = ARRAY of compact cells
    if type_code == 6:
        return [_decode_compact_cell(v) for v in (value or [])]
    # 12 = VECTOR — list of bytes-strings, each is the float repr.
    if type_code == 12:
        out_v: list[float] = []
        for v in value or []:
            if isinstance(v, (bytes, bytearray)):
                try:
                    out_v.append(float(v.decode("utf-8")))
                except (ValueError, UnicodeDecodeError):
                    pass
            else:
                try:
                    out_v.append(float(v))
                except (TypeError, ValueError):
                    pass
        return out_v
    # Fallback — return the raw value so the caller can see something.
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    if isinstance(value, list):
        return [_decode_compact_cell(v) for v in value]
    return value


async def query_compact(r: redis_async.Redis, db: str, cypher: str) -> list[dict[str, Any]]:
    """Run a Cypher query in ``--compact`` mode, return rows as dicts.

    Compact mode is the only reliable way to get vectors / arrays back
    from FalkorDB with their types preserved. Non-compact silently
    serializes them to a string.
    """
    raw = await r.execute_command("GRAPH.RO_QUERY", db, cypher, "--compact")
    if not isinstance(raw, list) or len(raw) < 2:
        return []
    headers_block = raw[0]
    rows_block = raw[1]
    header_names: list[str] = []
    for h in headers_block:
        # compact header is [column_type, name_bytes]
        name_raw = h[1] if isinstance(h, list) and len(h) >= 2 else h
        if isinstance(name_raw, (bytes, bytearray)):
            header_names.append(name_raw.decode("utf-8"))
        else:
            header_names.append(str(name_raw))
    out: list[dict[str, Any]] = []
    for row in rows_block:
        record: dict[str, Any] = {}
        for name, cell in zip(header_names, row):
            record[name] = _decode_compact_cell(cell)
        out.append(record)
    return out


# Back-compat alias for the count helper.
async def query_uncompact(r: redis_async.Redis, db: str, cypher: str) -> list[dict[str, Any]]:
    """Run a Cypher query and return rows as dicts (uses compact mode
    under the hood for type fidelity)."""
    return await query_compact(r, db, cypher)


# ---------- migration --------------------------------------------------------

LABELS_NODES = ["Episodic", "Entity"]
LABELS_EDGES = ["RELATES_TO", "MENTIONS"]


# FalkorDB's non-compact RETURN serializes ``properties(n)`` to a single
# string, not a usable map. So we enumerate every property column we
# need explicitly. These lists match Graphiti's current schema; if
# Graphiti adds a column we don't list, it's lost in the migration.
EPISODIC_COLS = [
    "uuid", "name", "group_id", "source_description", "source",
    "content", "entity_edges", "created_at", "valid_at",
]
ENTITY_COLS = [
    "uuid", "name", "group_id", "summary", "labels", "attributes",
    "name_embedding", "created_at",
]
RELATES_TO_COLS = [
    "uuid", "name", "fact", "fact_embedding", "group_id",
    "episodes", "created_at", "valid_at", "expired_at", "invalid_at",
]
MENTIONS_COLS = ["uuid", "group_id", "created_at"]

NODE_COLS = {"Episodic": EPISODIC_COLS, "Entity": ENTITY_COLS}
EDGE_COLS = {"RELATES_TO": RELATES_TO_COLS, "MENTIONS": MENTIONS_COLS}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="actually write to Neo4j (default: dry-run count)")
    ap.add_argument("--batch", type=int, default=200,
                    help="batch size for UNWIND CREATE (default 200)")
    args = ap.parse_args()

    falk = redis_async.Redis(
        host=os.environ.get("FALKORDB_HOST", "localhost"),
        port=int(os.environ.get("FALKORDB_PORT", "6379")),
        decode_responses=False,
    )
    fdb = os.environ.get("FALKORDB_DATABASE", "lighthouse")

    neo = AsyncGraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]),
    )
    ndb = os.environ.get("NEO4J_DATABASE", "neo4j")

    # 1. Count.
    counts: dict[str, int] = {}
    for label in LABELS_NODES:
        rows = await query_uncompact(falk, fdb, f"MATCH (n:{label}) RETURN count(n) AS c")
        counts[f"node:{label}"] = int(rows[0]["c"]) if rows else 0
    for rel in LABELS_EDGES:
        rows = await query_uncompact(falk, fdb, f"MATCH ()-[r:{rel}]->() RETURN count(r) AS c")
        counts[f"edge:{rel}"] = int(rows[0]["c"]) if rows else 0
    print("source counts:")
    for k, v in counts.items():
        print(f"  {k:20s} {v}")

    if not args.apply:
        print("\n(dry-run; pass --apply to migrate)")
        await falk.aclose()
        await neo.close()
        return 0

    # 2. Migrate nodes (one label at a time, paginate). Use
    #    ``driver.execute_query`` which auto-commits — bare
    #    ``session.run`` doesn't consume the result and Neo4j
    #    rolls back at session close.
    for label in LABELS_NODES:
        cols = NODE_COLS[label]
        proj = ", ".join(f"n.{c} AS {c}" for c in cols)
        n = counts[f"node:{label}"]
        print(f"\nmigrating {n} {label} nodes...")
        offset = 0
        while offset < n:
            rows = await query_uncompact(
                falk,
                fdb,
                f"MATCH (n:{label}) RETURN {proj} "
                f"SKIP {offset} LIMIT {args.batch}",
            )
            if not rows:
                break
            # Drop nulls — Neo4j rejects setting a property to None via
            # ``SET n = row`` if row has nulls.
            batch = [
                {k: v for k, v in r.items() if v is not None and v != ""}
                for r in rows
            ]
            batch = [p for p in batch if p.get("uuid")]
            await neo.execute_query(
                f"UNWIND $rows AS row CREATE (n:{label}) SET n = row",
                rows=batch,
                database_=ndb,
            )
            offset += len(rows)
            print(f"  {offset}/{n}")

    # Create an index on uuid for both labels so the edge MATCH
    # by-uuid is fast (otherwise it's an O(n) scan per row).
    for label in LABELS_NODES:
        await neo.execute_query(
            f"CREATE INDEX {label.lower()}_uuid IF NOT EXISTS "
            f"FOR (n:{label}) ON (n.uuid)",
            database_=ndb,
        )

    # 3. Migrate edges.
    for rel in LABELS_EDGES:
        cols = EDGE_COLS[rel]
        proj = ", ".join(f"r.{c} AS {c}" for c in cols)
        n = counts[f"edge:{rel}"]
        print(f"\nmigrating {n} {rel} edges...")
        offset = 0
        while offset < n:
            rows = await query_uncompact(
                falk,
                fdb,
                f"MATCH (a)-[r:{rel}]->(b) "
                f"RETURN a.uuid AS src, b.uuid AS dst, {proj} "
                f"SKIP {offset} LIMIT {args.batch}",
            )
            if not rows:
                break
            batch = []
            for r in rows:
                if not (r.get("src") and r.get("dst")):
                    continue
                props = {k: r[k] for k in cols if r.get(k) is not None and r.get(k) != ""}
                batch.append({"src": r["src"], "dst": r["dst"], "props": props})
            await neo.execute_query(
                f"UNWIND $rows AS row "
                f"MATCH (a {{uuid: row.src}}), (b {{uuid: row.dst}}) "
                f"CREATE (a)-[r:{rel}]->(b) SET r = row.props",
                rows=batch,
                database_=ndb,
            )
            offset += len(rows)
            print(f"  {offset}/{n}")

    # 4. Verify counts on Neo4j side.
    print("\nverifying...")
    for label in LABELS_NODES:
        res = await neo.execute_query(
            f"MATCH (n:{label}) RETURN count(n) AS c",
            database_=ndb,
        )
        c = res.records[0]["c"] if res.records else 0
        print(f"  neo4j node:{label:10s} {c} (src={counts[f'node:{label}']})")
    for rel in LABELS_EDGES:
        res = await neo.execute_query(
            f"MATCH ()-[r:{rel}]->() RETURN count(r) AS c",
            database_=ndb,
        )
        c = res.records[0]["c"] if res.records else 0
        print(f"  neo4j edge:{rel:11s} {c} (src={counts[f'edge:{rel}']})")

    await falk.aclose()
    await neo.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
