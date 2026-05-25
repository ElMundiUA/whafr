"""Async summary-worker — decouples chunk enrichment from ingest.

Main ingest writes a chunk into pg with ``summary``/``tags``/
``keywords`` left NULL. The worker polls for chunks where any of
those is NULL, batches them through OpenRouter Qwen-2.5-7B for
one combined SUMMARY + TAGS + KEYWORDS extraction, and updates the
row. The boosted tsvector (``tsv_boosted``) auto-refreshes via the
GENERATED column.

Why a worker, not inline ingest:
- Main ingest path (FlatGraph.upsert_document) does 1 embedding
  call per doc. Adding a synchronous Qwen call would 2-3× the
  wall-clock per doc and couple ingest latency to OpenRouter
  availability.
- Worker can batch — pull N pending chunks, fire N concurrent
  OpenRouter calls, write N updates. Ingest stays simple.
- Worker is restartable / catches up on backlog after downtime.
  Main ingest doesn't care if the worker is paused.

Schedule pattern: this script is meant to run as a k8s CronJob
every 5 minutes with a process limit so two workers don't fight
over the same rows.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time

logger = logging.getLogger(__name__)


async def main(
    *, batch_size: int, concurrency: int, max_runtime_sec: int
) -> int:
    import httpx

    from lighthouse.core.enrichment import (
        MODEL,
        OPENROUTER_BASE,
        PROMPT,
    )
    from lighthouse.core.enrichment import (
        parse as parse_enrichment,
    )
    from lighthouse.core.flat_graph import FlatGraph

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        logger.error("OPENROUTER_API_KEY missing — worker can't run")
        return 1

    flat = FlatGraph()
    await flat.initialize()
    pool = await flat._pool_lazy()

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://lighthouse.harborgang.com",
        "X-Title": "lighthouse-summary-worker",
    }
    sem = asyncio.Semaphore(int(concurrency))
    deadline = time.monotonic() + max_runtime_sec
    total_written = 0
    total_failed = 0

    async with httpx.AsyncClient(
        base_url=OPENROUTER_BASE, headers=headers, timeout=120.0
    ) as client:

        while True:
            if time.monotonic() >= deadline:
                logger.info("hit max_runtime_sec — exiting cleanly")
                break

            # Pull next batch. Filter by keywords IS NULL because
            # that's the canonical "still needs enrichment" flag —
            # captures fresh chunks (no summary at all) AND legacy
            # ones (summary+tags but no keywords yet).
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT uuid, source, content
                    FROM chunks
                    WHERE keywords IS NULL
                      AND length(content) BETWEEN 200 AND 12000
                    ORDER BY ingested_at
                    LIMIT $1
                    """,
                    int(batch_size),
                )
            if not rows:
                logger.info(
                    "no pending chunks — total written=%d failed=%d",
                    total_written,
                    total_failed,
                )
                break

            written = failed = 0

            async def one(row) -> None:
                nonlocal written, failed
                async with sem:
                    try:
                        resp = await client.post(
                            "/chat/completions",
                            json={
                                "model": MODEL,
                                "messages": [
                                    {
                                        "role": "user",
                                        "content": PROMPT.format(
                                            body=row["content"]
                                        ),
                                    }
                                ],
                                "temperature": 0.2,
                                "max_tokens": 220,
                            },
                        )
                        resp.raise_for_status()
                        text = (
                            resp.json()
                            .get("choices", [{}])[0]
                            .get("message", {})
                            .get("content", "")
                        ).strip()
                    except Exception:
                        logger.exception(
                            "openrouter call failed for %s", row["source"]
                        )
                        failed += 1
                        return
                    summary, tags, keywords = parse_enrichment(text)
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

            await asyncio.gather(*(one(r) for r in rows))
            total_written += written
            total_failed += failed
            logger.info(
                "batch: written=%d failed=%d (total written=%d failed=%d)",
                written,
                failed,
                total_written,
                total_failed,
            )
    await flat.close()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        help="Chunks pulled per batch (default 200).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=15,
        help="Parallel OpenRouter calls (default 15).",
    )
    parser.add_argument(
        "--max-runtime-sec",
        type=int,
        default=1800,
        help="Hard cap on wall-time (default 30 min). The CronJob "
        "fires every 5 min; this just stops a runaway worker.",
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    sys.exit(
        asyncio.run(
            main(
                batch_size=args.batch_size,
                concurrency=args.concurrency,
                max_runtime_sec=args.max_runtime_sec,
            )
        )
    )
