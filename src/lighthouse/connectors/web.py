"""Web page connector.

Thin wrapper around LlamaIndex's ``BeautifulSoupWebReader`` so we can
ingest arbitrary HTML pages (tech blogs, RFCs, docs that don't live in
a git-cloneable repo) without writing a fetcher from scratch.

What this *doesn't* handle:

- **JavaScript-rendered pages.** BeautifulSoup parses static HTML; SPAs
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
from typing import Any

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

        # Lazy import so the optional dep doesn't block module import
        # for users who only need the markdown connector.
        from llama_index.readers.web import BeautifulSoupWebReader

        reader = BeautifulSoupWebReader()
        docs: list[Any] = reader.load_data(self._urls)
        for doc in docs:
            meta = doc.metadata or {}
            url = str(meta.get("URL") or meta.get("url") or "")
            yield SourceDocument(
                source_id=url or str(doc.id_),
                title=str(meta.get("title") or url or doc.id_),
                body=doc.text,
                url=url or None,
                reference_time=None,
                metadata={str(k): str(v) for k, v in meta.items()},
            )
