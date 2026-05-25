"""Cheap-LLM relevance gate.

Sits between the connector's :class:`SourceDocument` stream and the
engine's embedding pass. Decides per-doc whether the content is worth
ingesting at all — runs a tiny ``gpt-4o-mini`` (or whichever
``RELEVANCE_GATE_MODEL`` is set to) classification call that costs
fractions of a cent per doc.

The economic argument: embedding a doc (chunk splits × embedding
calls) costs more than the gate's single ~$0.0002 classification.
Filtering out 20% off-topic crawl noise pays for the gate many times
over on whole-site crawls.

The gate is opt-in via ``RELEVANCE_GATE_ENABLED=true``. Reason:
hand-curated source lists (the ``web`` connector with explicit URLs)
don't need it — the curator already vouched. Whole-site crawls and
aggregator sources are where the gate earns its keep.
"""

from __future__ import annotations

import logging
from typing import Any

from lighthouse.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


_PROMPT = """\
You decide whether a document should be indexed into a technical
knowledge graph for AI software-engineering agents (Planning,
Developer, Reviewer, QA, Designer, PM, BA roles).

REJECT if the document is:
- A login / signup / pricing / marketing landing page
- A cookie banner / privacy policy / TOS
- A 404 / "page not found" / error
- A non-English aggregator / clickbait article with no concrete
  technical content
- A bare site index with no body content
- An ad / sponsored placement

ACCEPT if the document contains:
- Technical reference material (API docs, framework guides)
- Engineering methodology / process / patterns
- Canonical thought leadership (Fowler, Cohn, Hettinger-style)
- Tutorials / how-tos with concrete code or steps
- Standards (RFC, IEEE, OWASP)

Reply with EXACTLY one word: "accept" or "reject". No explanation.
"""


class RelevanceGate:
    """Async classifier — accept/reject per document."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: Any | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._settings.relevance_gate_enabled)

    async def _client_lazy(self) -> Any:
        if self._client is not None:
            return self._client
        from openai import AsyncOpenAI

        if not self._settings.openai_api_key:
            raise RuntimeError(
                "OPENAI_API_KEY not set — relevance gate can't classify"
            )
        self._client = AsyncOpenAI(api_key=self._settings.openai_api_key)
        return self._client

    async def accept(self, *, title: str, body: str) -> bool:
        """Returns True if doc should be indexed.

        On any error (network, timeout, malformed response) we default
        to True — don't lose ingest work because the classifier
        choked. The gate is an optimisation, not a correctness gate.
        """
        if not self.enabled:
            return True
        client = await self._client_lazy()
        # Cap body to first ~3KB so the gate call stays cheap even on
        # large crawled pages. Three KB is enough for the model to
        # distinguish API doc from cookie banner.
        snippet = body[:3000]
        try:
            resp = await client.chat.completions.create(
                model=self._settings.relevance_gate_model,
                max_tokens=4,
                temperature=0.0,
                messages=[
                    {"role": "system", "content": _PROMPT},
                    {
                        "role": "user",
                        "content": f"TITLE: {title}\n\nBODY:\n{snippet}",
                    },
                ],
            )
            verdict = (resp.choices[0].message.content or "").strip().lower()
            return verdict.startswith("accept")
        except Exception:
            logger.exception(
                "relevance gate errored on title=%s — defaulting to accept",
                title,
            )
            return True
