"""Generate one-sentence summary + tags for chunks via OpenRouter Qwen.

For the B2 A/B test we summarise chunks from opinion-heavy domains
(clarification / PM / self-heal / architecture / decomposition)
where flat-RAG currently loses ground vs Graphiti, then check whether
having that summary boosts the matching search results.

Default: 250 chunks, distributed across the targeted source prefixes
so the test covers the breadth of the regression rather than one
narrow corner. Cost on Qwen-2.5-7B via OpenRouter:
~$0.00014 / chunk × 250 ≈ $0.04.

Idempotent: chunks that already have ``summary`` populated are
skipped. Re-run to fill gaps after schema evolution.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from lighthouse.core.enrichment import MODEL, OPENROUTER_BASE, PROMPT, parse

logger = logging.getLogger(__name__)


# Source prefixes covering opinion-heavy / methodology-heavy roles
# where flat-RAG audit currently lags Graphiti. Picked by inspecting
# the per-domain breakdown in /tmp/flat-audit-postmigration.md.
DEFAULT_PREFIXES = (
    "clarification",
    "product-manager",
    "pm",
    "self-heal",
    "self_heal",
    "selfheal",
    "architecture",
    "decomposition",
    "planning",
)


async def main(
    *, limit: int, prefixes: tuple[str, ...], concurrency: int = 10
) -> int:
    import httpx

    from lighthouse.core.flat_graph import FlatGraph

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        logger.error("OPENROUTER_API_KEY missing")
        return 1

    flat = FlatGraph()
    await flat.initialize()
    pool = await flat._pool_lazy()

    # Pull chunks that NEED enrichment — keywords IS NULL covers the
    # full-corpus rewrite path (chunks that had only summary+tags from
    # the first pass also need keywords) AND fresh chunks that have
    # nothing yet. Empty prefixes => match everything.
    async with pool.acquire() as conn:
        if prefixes:
            rows = await conn.fetch(
                """
                SELECT uuid, source, content
                FROM chunks
                WHERE keywords IS NULL
                  AND length(content) BETWEEN 200 AND 12000
                  AND EXISTS (
                    SELECT 1 FROM unnest($1::text[]) AS p
                    WHERE chunks.source LIKE p || '%'
                  )
                ORDER BY random()
                LIMIT $2
                """,
                list(prefixes),
                int(limit),
            )
        else:
            rows = await conn.fetch(
                """
                SELECT uuid, source, content
                FROM chunks
                WHERE keywords IS NULL
                  AND length(content) BETWEEN 200 AND 12000
                ORDER BY ingested_at
                LIMIT $1
                """,
                int(limit),
            )

    if not rows:
        logger.warning(
            "no chunks matched prefixes %s — nothing to summarise", prefixes
        )
        await flat.close()
        return 0
    logger.info("summarising %d chunks", len(rows))

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://lighthouse.harborgang.com",
        "X-Title": "lighthouse-summary",
    }
    sem = asyncio.Semaphore(int(concurrency))
    written = 0
    failed = 0

    async with httpx.AsyncClient(
        base_url=OPENROUTER_BASE, headers=headers, timeout=120.0
    ) as client:

        async def one(row) -> None:
            nonlocal written, failed
            async with sem:
                body = row["content"]
                try:
                    resp = await client.post(
                        "/chat/completions",
                        json={
                            "model": MODEL,
                            "messages": [
                                {
                                    "role": "user",
                                    "content": PROMPT.format(body=body),
                                }
                            ],
                            "temperature": 0.2,
                            "max_tokens": 200,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    text = (
                        data.get("choices", [{}])[0]
                        .get("message", {})
                        .get("content", "")
                    ).strip()
                except Exception:
                    logger.exception("OpenRouter call failed for %s", row["source"])
                    failed += 1
                    return

                summary, tags, keywords = parse(text)
                if not summary:
                    failed += 1
                    return

                async with pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE chunks SET summary = $2, tags = $3, "
                        "keywords = $4 WHERE uuid = $1",
                        row["uuid"],
                        summary,
                        tags,
                        keywords,
                    )
                written += 1
                if written % 20 == 0:
                    logger.info(
                        "summarised %d / %d (failed=%d)",
                        written,
                        len(rows),
                        failed,
                    )

        await asyncio.gather(*(one(r) for r in rows))

    await flat.close()
    logger.info("done — written=%d failed=%d", written, failed)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=250)
    parser.add_argument(
        "--prefixes",
        nargs="*",
        default=DEFAULT_PREFIXES,
        help="Source prefixes to target (default: opinion-heavy roles). "
        "Pass --prefixes with no args to summarise the full corpus.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Parallel OpenRouter calls (default 10)",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    sys.exit(
        asyncio.run(
            main(
                limit=args.limit,
                prefixes=tuple(args.prefixes),
                concurrency=args.concurrency,
            )
        )
    )
