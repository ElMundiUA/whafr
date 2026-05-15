"""Web page connector.

Two extraction backends, picked per URL:

- **HTML pages** → ``trafilatura`` strips boilerplate (nav/footer/cookie
  walls) and returns clean article body.
- **PDF documents** → ``docling-serve`` (an external sidecar) handles
  layout-aware parsing — far better than naive PDF-to-text on
  paginated whitepapers and books.

The routing key is the URL's extension. The decision is deliberately
mechanical (not Content-Type sniffed) so a dry-run of which URLs would
use which backend matches what actually runs. If docling-serve is
unreachable we log and skip the PDF rather than fall back to a worse
backend that would poison entity extraction with garbage.

What this *still doesn't* handle:

- **JavaScript-rendered HTML pages.** Trafilatura parses static HTML;
  SPAs need a real browser (Playwright). Add when needed.
- **Crawling.** This connector ingests the exact URLs you pass it.
  Spider a whole docs site? Use the ``SitemapCrawlConnector``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterable
from urllib.parse import urlparse

from lighthouse.connectors.base import Connector, SourceDocument
from lighthouse.core.config import get_settings

logger = logging.getLogger(__name__)


def _is_pdf_url(url: str) -> bool:
    """True if the URL's path ends with ``.pdf`` (case-insensitive).

    Deliberately simple — we don't sniff Content-Type because that adds
    a round-trip and rarely changes the outcome in practice. Sources
    we control put the extension on the URL.
    """
    path = urlparse(url).path or ""
    return path.lower().endswith(".pdf")


class WebConnector(Connector):
    name = "web"

    def __init__(self, urls: Iterable[str]) -> None:
        # Materialise eagerly — callers usually pass a generator we'd
        # only get to iterate once otherwise.
        self._urls = list(urls)

    async def ingest(self) -> AsyncIterator[SourceDocument]:
        if not self._urls:
            logger.warning("web connector got no URLs — yielding zero docs")
            return

        for url in self._urls:
            try:
                if _is_pdf_url(url):
                    doc = await self._extract_pdf(url)
                else:
                    doc = self._extract_html(url)
                if doc is not None:
                    yield doc
            except Exception:
                logger.exception("failed to extract %s", url)
                continue

    # --- HTML ---------------------------------------------------------

    def _extract_html(self, url: str) -> SourceDocument | None:
        # Lazy import so the optional dep doesn't block module import.
        import trafilatura

        raw = trafilatura.fetch_url(url)
        if not raw:
            logger.warning("trafilatura got no body for %s", url)
            return None
        # ``include_comments=False`` strips reader comments;
        # ``favor_precision=True`` errs toward smaller-but-cleaner text
        # rather than over-grabbing borderline content.
        text = trafilatura.extract(
            raw,
            include_comments=False,
            favor_precision=True,
            output_format="txt",
        )
        if not text or len(text.strip()) < 100:
            logger.warning(
                "trafilatura extracted only %s chars for %s — skipping",
                len(text or ""),
                url,
            )
            return None
        meta = trafilatura.extract_metadata(raw)
        title = (meta.title if meta and meta.title else url)
        return SourceDocument(
            source_id=url,
            title=str(title),
            body=text,
            url=url,
            reference_time=None,
            metadata={
                "url": url,
                "title": str(title),
                "author": str(meta.author) if meta and meta.author else "",
                "date": str(meta.date) if meta and meta.date else "",
                "extractor": "trafilatura",
            },
        )

    # --- PDF ----------------------------------------------------------

    async def _extract_pdf(self, url: str) -> SourceDocument | None:
        settings = get_settings()
        docling_url = (settings.lighthouse_docling_url or "").rstrip("/")
        if not docling_url:
            logger.warning("PDF %s skipped — LIGHTHOUSE_DOCLING_URL is empty", url)
            return None

        import httpx

        # Docling supports a one-shot conversion endpoint that takes a
        # remote URL and returns the converted document inline. We
        # request markdown — Graphiti's entity extractor handles
        # markdown well and we keep payloads small.
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.post(
                    f"{docling_url}/v1/convert/source",
                    json={
                        "sources": [{"kind": "http", "url": url}],
                        "options": {"to_formats": ["md"]},
                    },
                )
                resp.raise_for_status()
                payload = resp.json()
        except (httpx.HTTPError, ValueError):
            logger.exception("docling-serve failed to convert %s", url)
            return None

        # Docling returns a `document` object whose `md_content` holds
        # the markdown body. Older versions emit `markdown` at the top
        # level — accept both shapes.
        body = (
            (payload.get("document") or {}).get("md_content")
            or payload.get("markdown")
            or ""
        )
        if not body or len(body.strip()) < 100:
            logger.warning(
                "docling extracted only %s chars for %s — skipping",
                len(body or ""),
                url,
            )
            return None

        return SourceDocument(
            source_id=url,
            title=url.rsplit("/", 1)[-1] or url,
            body=body,
            url=url,
            reference_time=None,
            metadata={"url": url, "extractor": "docling"},
        )
