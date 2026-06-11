"""LLM usefulness scoring for search hits.

Shared by two consumers:

- the weekly coverage audit (``lighthouse coverage-audit``), which
  sweeps a curated query list, and
- the per-search gap classifier in :mod:`lighthouse.core.query_log`,
  which scores live traffic when ``lighthouse_gap_classifier_enabled``
  is on (kapa-style "uncertain answers": hits came back, but none of
  them actually grounds an answer).

Cosine/RRF scores are deliberately not used for this: their scale
shifts with corpus size and reranker settings, while a cheap Haiku
rating of "would this help an engineer answer the query?" has stayed
stable across audit waves.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Threshold below which a query result is considered "not useful enough
# to ground an answer". 3.0/5 is the audit convention used in the
# series of manual audit waves (avg of 5 hits scored 1-5).
USEFUL_THRESHOLD = 3.0


async def score_hits(query: str, summaries: list[str]) -> list[int]:
    """Ask Claude Haiku to rate each hit 1..5 for usefulness vs query.

    Returns one int per summary, in the same order. On API failure
    returns all zeros (counted as gap) rather than aborting the caller.
    """
    from anthropic import AsyncAnthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY missing — scoring everything as 0")
        return [0] * len(summaries)
    client = AsyncAnthropic(api_key=api_key)

    bullets = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(summaries))
    prompt = (
        f"Query: {query!r}\n\n"
        f"Candidate facts retrieved from a knowledge base:\n{bullets}\n\n"
        "Rate EACH candidate 1-5 on whether it would help an engineer "
        "answer the query:\n"
        "  1 = irrelevant or wrong topic\n"
        "  3 = on-topic but generic\n"
        "  5 = directly answers / canonical reference\n\n"
        f"Reply with EXACTLY {len(summaries)} integers separated by spaces. "
        "No prose. Example: '4 2 5 1 3'."
    )
    try:
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=80,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            b.text for b in resp.content if getattr(b, "type", "") == "text"
        ).strip()
    except Exception:
        logger.exception("scoring %r failed — treating as zero", query)
        return [0] * len(summaries)

    out: list[int] = []
    for tok in text.replace(",", " ").split():
        try:
            n = int(tok)
            out.append(max(1, min(n, 5)))
        except ValueError:
            continue
    # Pad / truncate so caller can rely on length alignment.
    if len(out) < len(summaries):
        out.extend([0] * (len(summaries) - len(out)))
    return out[: len(summaries)]
