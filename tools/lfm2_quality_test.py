"""Quality test for Liquid LFM2.5-1.2B-Instruct on chunk summarisation.

Picks 20 representative chunks from the flat-RAG corpus (varied by
source — RFC, OWASP, FastAPI docs, SRE Book, blog posts), asks
LFM2 to produce a one-sentence summary + 3-5 topic tags, and has
Claude Haiku judge each output 0-10 for usefulness.

Outputs:
- /tmp/lfm2-quality/results.json — raw per-chunk results
- /tmp/lfm2-quality/summary.md — headline numbers + verdict

Acceptance: mean usefulness >= 7/10 → wire as opt-in chunk-summary
field. < 7 → skip the layer, accept flat as the final state.

Run (cluster ollama via port-forward):
    kubectl port-forward -n lighthouse svc/ollama-lfm2 11434:80 &
    uv run python tools/lfm2_quality_test.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)


OUT_DIR = Path("/tmp/lfm2-quality")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


SUMMARISE_PROMPT = """You summarise technical documentation. Given a passage, output:

SUMMARY: one short sentence (under 30 words) capturing what this passage tells an engineer.
TAGS: 3 to 5 short topic tags, comma-separated (e.g. "OAuth, JWT, PKCE, RFC-7636").

PASSAGE:
\"\"\"
{body}
\"\"\"

Format exactly:
SUMMARY: <one sentence>
TAGS: <tag1>, <tag2>, <tag3>"""


JUDGE_PROMPT = """Rate the SUMMARY + TAGS combo below for retrieval usefulness.

Original passage (truncated):
\"\"\"
{body}
\"\"\"

Generated summary: {summary}
Generated tags: {tags}

Score 0-10:
  10 = summary is accurate and specific; tags would surface this on a relevant search
  7  = summary is correct but generic; tags are okay
  4  = summary is partially wrong or vague; tags partially useful
  1  = summary is hallucinated or misleading; tags off-topic

Reply with EXACTLY one integer 0-10. No prose."""


async def main() -> int:
    import httpx
    from anthropic import AsyncAnthropic

    from lighthouse.core.flat_graph import FlatGraph

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.error("ANTHROPIC_API_KEY missing — judge can't run")
        return 1
    judge = AsyncAnthropic(api_key=api_key)

    flat = FlatGraph()
    pool = await flat._pool_lazy()
    async with pool.acquire() as conn:
        # Pick 20 chunks across distinct source prefixes — gets us
        # variety (RFCs, OWASP, FastAPI, blogs, releases).
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (split_part(source, ':', 1))
                uuid, name, source, content
            FROM chunks
            WHERE length(content) BETWEEN 400 AND 6000
            ORDER BY split_part(source, ':', 1), random()
            LIMIT 20
            """
        )
    await flat.close()
    if not rows:
        logger.error("no chunks available — populate flat corpus first")
        return 1
    logger.info("sampled %d chunks across distinct source prefixes", len(rows))

    results = []
    async with httpx.AsyncClient(timeout=120.0, base_url=OLLAMA_URL) as ollama:
        for i, row in enumerate(rows, 1):
            body = row["content"]
            t0 = time.monotonic()
            try:
                gen = await ollama.post(
                    "/api/generate",
                    json={
                        "model": "lfm2",
                        "prompt": SUMMARISE_PROMPT.format(body=body),
                        "stream": False,
                        "options": {
                            "temperature": 0.2,
                            "num_predict": 200,
                        },
                    },
                )
                gen.raise_for_status()
                lfm_out = (gen.json().get("response") or "").strip()
            except Exception as e:
                logger.exception("lfm2 generate failed for %s", row["source"])
                lfm_out = f"<error: {e}>"
            latency = (time.monotonic() - t0) * 1000

            summary, tags = _parse_lfm_output(lfm_out)
            # Judge with Claude Haiku
            score = await _judge(
                judge,
                body=body[:2000],
                summary=summary,
                tags=tags,
            )
            row_result = {
                "index": i,
                "source": row["source"][:60],
                "name": row["name"][:60],
                "body_chars": len(body),
                "lfm_raw_output": lfm_out[:400],
                "summary": summary,
                "tags": tags,
                "judge_score": score,
                "lfm_latency_ms": int(latency),
            }
            results.append(row_result)
            print(
                f"[{i:>2}/20] {row['source'][:35]:<35} "
                f"latency={latency / 1000:.1f}s judge={score}/10 "
                f"summary={summary[:60]!r}"
            )

    # headline
    n_scored = sum(1 for r in results if r["judge_score"] is not None)
    mean = (
        sum(r["judge_score"] for r in results if r["judge_score"] is not None)
        / max(1, n_scored)
    )
    median_latency = sorted(r["lfm_latency_ms"] for r in results)[len(results) // 2]
    p95_latency = sorted(r["lfm_latency_ms"] for r in results)[
        int(len(results) * 0.95)
    ]

    out_json = OUT_DIR / "results.json"
    out_json.write_text(
        json.dumps(
            {
                "n_chunks": len(results),
                "n_scored": n_scored,
                "mean_score": round(mean, 2),
                "median_latency_ms": median_latency,
                "p95_latency_ms": p95_latency,
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    summary_md = OUT_DIR / "summary.md"
    verdict = (
        "ACCEPT — wire as opt-in chunk-summary field"
        if mean >= 7.0
        else "REJECT — skip the local-summary layer, accept flat as final"
    )
    summary_md.write_text(
        "\n".join(
            [
                "# LFM2.5-1.2B-Instruct quality test",
                "",
                f"- Chunks tested: {len(results)}",
                f"- Mean judge score: **{mean:.2f} / 10**",
                f"- Median LFM2 latency: {median_latency} ms",
                f"- p95 LFM2 latency: {p95_latency} ms",
                f"- Verdict: **{verdict}**",
                "",
                "## Per-chunk scores",
                "",
                "| # | source | score | latency | summary |",
                "|---|---|---:|---:|---|",
                *[
                    f"| {r['index']} | `{r['source']}` | {r['judge_score']}/10 "
                    f"| {r['lfm_latency_ms']}ms | {r['summary'][:80]} |"
                    for r in results
                ],
            ]
        ),
        encoding="utf-8",
    )
    print()
    print(f"wrote {out_json}")
    print(f"wrote {summary_md}")
    print(f"VERDICT: {verdict}")
    return 0


def _parse_lfm_output(text: str) -> tuple[str, str]:
    summary, tags = "", ""
    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("SUMMARY:"):
            summary = line.split(":", 1)[1].strip()
        elif line.upper().startswith("TAGS:"):
            tags = line.split(":", 1)[1].strip()
    return summary or text[:120], tags


async def _judge(client, *, body: str, summary: str, tags: str) -> int | None:
    try:
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            temperature=0,
            messages=[
                {
                    "role": "user",
                    "content": JUDGE_PROMPT.format(
                        body=body, summary=summary, tags=tags
                    ),
                }
            ],
        )
        text = "".join(
            b.text for b in resp.content if getattr(b, "type", "") == "text"
        ).strip()
        for tok in text.split():
            try:
                n = int(tok)
                return max(0, min(10, n))
            except ValueError:
                continue
        return None
    except Exception:
        logger.exception("judge call failed")
        return None


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )
    sys.exit(asyncio.run(main()))
