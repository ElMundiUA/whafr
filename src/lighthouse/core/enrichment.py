"""Chunk-enrichment prompt + response parser.

Shared between the one-shot bulk generator (``tools/
generate_chunk_summaries.py``) and the recurring async worker
(``lighthouse.runner.summary_worker``). Keeping the prompt in one
place means changing the format propagates to both call sites
without drift.

The model is fixed to Qwen-2.5-7B-Instruct via OpenRouter — it
scored 7.0/10 on our quality test (LFM2.5-1.2B was 4.5/10), at
~$0.00014 per chunk. ``KEYWORDS`` is the expansion field that
catches query phrasings the body itself doesn't use; weighted
equally to ``SUMMARY`` in ``tsv_boosted`` so it can rescue
terminology mismatches between query and source.
"""

from __future__ import annotations

import re


OPENROUTER_BASE = "https://openrouter.ai/api/v1"
MODEL = "qwen/qwen-2.5-7b-instruct"


PROMPT = """You enrich technical documentation for retrieval. Given a passage, output three fields:

SUMMARY: one short sentence (under 30 words) capturing what an engineer would learn from this passage.
TAGS: 3 to 5 short topic labels, comma-separated, naming what this passage is *about* (e.g. "OAuth, JWT, PKCE, RFC-7636").
KEYWORDS: 6 to 12 search-relevant terms a developer might TYPE when looking for this content — include synonyms, alternative phrasings, related concepts, and exact terms NOT already prominent in the passage. Comma-separated. (e.g. "code_challenge, code_verifier, S256, authorization code grant, public client auth, mobile app auth, native app, SPA login, single-page auth, OAuth flow").

PASSAGE:
\"\"\"
{body}
\"\"\"

Format exactly:
SUMMARY: <one sentence>
TAGS: <tag1>, <tag2>, ...
KEYWORDS: <kw1>, <kw2>, ..."""


def parse(text: str) -> tuple[str, str, str]:
    """Parse ``SUMMARY: / TAGS: / KEYWORDS:`` from the model's reply.
    Returns empty strings for any missing field. Tolerant of leading
    junk lines (chain-of-thought, "ANSWER:" preambles, etc.)."""
    summary, tags, keywords = "", "", ""
    for line in text.splitlines():
        line = line.strip()
        if re.match(r"summary[:\-]", line, re.IGNORECASE):
            summary = line.split(":", 1)[-1].strip()
        elif re.match(r"tags[:\-]", line, re.IGNORECASE):
            tags = line.split(":", 1)[-1].strip()
        elif re.match(r"keywords[:\-]", line, re.IGNORECASE):
            keywords = line.split(":", 1)[-1].strip()
    return summary, tags, keywords
