"""Web page connector.

Uses ``trafilatura`` to extract the main article body and discard
boilerplate (nav, footer, sidebars, cookie banners) before handing
the text to the graph. The earlier ``BeautifulSoupWebReader``-backed
version polluted entity extraction with page chrome ("Home",
"Presentations", "Cloudflare") because it returned every visible
string on the page — trafilatura is purpose-built for article
extraction and produces dramatically cleaner episodes.

What this *doesn't* handle:

- **JavaScript-rendered pages.** Trafilatura parses static HTML; SPAs
  need a real browser. If/when we need that, the swap is to a
  Firecrawl/Crawl4AI connector — the surface stays identical.
- **Crawling.** This connector ingests the exact URLs you pass it,
  one document per URL. Spidering a whole docs site is the
  responsibility of a separate ``CrawlConnector`` we'll add when we
  hit a source that needs it.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterable

from lighthouse.connectors.base import Connector, SourceDocument

logger = logging.getLogger(__name__)


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

        # Lazy import so the optional dep doesn't block module import.
        import trafilatura

        for url in self._urls:
            try:
                raw = trafilatura.fetch_url(url)
                if not raw:
                    logger.warning("trafilatura got no body for %s", url)
                    continue
                # ``include_comments=False`` strips reader comments;
                # ``favor_precision=True`` errs toward smaller-but-cleaner
                # text rather than over-grabbing borderline content.
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
                    continue
                # Pull metadata (title, author, date) so we can populate
                # SourceDocument fields properly. Falls back gracefully.
                meta = trafilatura.extract_metadata(raw)
                title = (meta.title if meta and meta.title else url)
                yield SourceDocument(
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
                    },
                )
            except Exception:
                logger.exception("failed to extract %s", url)
                continue
