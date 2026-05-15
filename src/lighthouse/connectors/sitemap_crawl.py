"""Sitemap-driven crawl connector — Firecrawl-free for static docs.

Most documentation sites (python.org, postgresql.org, martinfowler.com,
nextjs.org, kubernetes.io, atlassian.com, …) are server-side rendered
and expose a ``sitemap.xml`` listing every URL. We don't need a
headless browser; we just enumerate the sitemap and feed each URL
through trafilatura.

This covers ~90% of what we'd pay Firecrawl for, free. The
``crawl`` (Firecrawl) connector stays for the remaining SPA / JS-
rendered cases (Linear docs, some Notion-rendered sites).

Per-source rate-limit is intentionally polite (1 req/s by default).
To still finish in reasonable time, run many sources concurrently —
each polite within its own domain, parallel across domains. The
``SourceScheduler``'s ``max_concurrent`` controls that fan-out.

Pipeline per source:

1. **Discover sitemap.** ``{root}/sitemap.xml`` → ``sitemap_index.xml``
   → ``robots.txt`` ``Sitemap:`` directives. Sitemap-index files are
   walked recursively to leaf sitemaps.
2. **Collect + filter URLs.** All ``<loc>`` entries, optionally
   restricted by ``include_paths`` (path-prefix match), capped at
   ``max_pages``.
3. **Extract politely.** Each URL → trafilatura (async-offloaded
   to a thread) at ``rate_limit_per_sec``.
4. **Log failures.** 404 / short-body / fetch-error / no-sitemap
   get appended to a JSONL file for later analysis.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import re
from collections.abc import AsyncIterator, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from lighthouse.connectors.base import Connector, SourceDocument
from lighthouse.core.config import get_settings

logger = logging.getLogger(__name__)


# Sitemap XML namespace. ElementTree returns namespaced tags like
# ``{ns}url`` — we match with a localname fallback so vendors that
# omit the namespace declaration (rare but seen) still parse.
_SM_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


class SitemapCrawlConnector(Connector):
    """Crawl a static-doc site through its ``sitemap.xml``."""

    name = "sitemap"

    def __init__(
        self,
        root: str,
        *,
        include_paths: Sequence[str] | None = None,
        max_pages: int = 200,
        rate_limit_per_sec: float = 1.0,
        sitemap_url: str | None = None,
    ) -> None:
        """
        Args:
            root: Site root URL, e.g. ``https://docs.python.org``.
            include_paths: URL-path prefixes to keep. ``["/3/library/"]``
                limits to that subtree. Empty/None means accept all
                URLs from the sitemap.
            max_pages: Hard cap on documents emitted.
            rate_limit_per_sec: Polite per-domain throttle. 1.0 is
                safe for almost everywhere; parallelism comes from
                running many sources concurrently, not from spamming
                one host.
            sitemap_url: Override sitemap discovery — fetch this URL
                directly. Useful when a site's sitemap is at a
                non-standard path.
        """
        self._root = root.rstrip("/")
        self._domain = urlparse(self._root).netloc
        self._include_paths = list(include_paths or [])
        self._max_pages = max_pages
        self._rate_limit = max(0.1, rate_limit_per_sec)
        self._override_sitemap = sitemap_url

    # ----- public ---------------------------------------------------

    async def ingest(self) -> AsyncIterator[SourceDocument]:
        import httpx

        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "lighthouse-sitemap-crawl/0.1"},
        ) as client:
            page_urls = await self._discover_and_walk(client)
        if not page_urls:
            logger.warning(
                "sitemap %s: discovered zero URLs (no sitemap or all filtered)",
                self._root,
            )
            self._log_failure(self._root, "no-urls-discovered")
            return

        page_urls = self._filter_and_cap(page_urls)
        logger.info(
            "sitemap %s: %d URLs to crawl (max_pages=%d, rate=%s/s)",
            self._root,
            len(page_urls),
            self._max_pages,
            self._rate_limit,
        )

        # trafilatura.fetch_url is synchronous — offload so the event
        # loop stays responsive when multiple sources run concurrently.
        delay = 1.0 / self._rate_limit
        for url in page_urls:
            doc = await asyncio.to_thread(self._extract, url)
            if doc is not None:
                yield doc
            await asyncio.sleep(delay)

    # ----- sitemap discovery + walking ------------------------------

    async def _discover_and_walk(self, client) -> list[str]:
        """Discover the sitemap(s) for this root and walk them to a
        flat list of page URLs.

        Tries (in order): explicit override → ``/sitemap.xml`` →
        ``/sitemap_index.xml`` → ``Sitemap:`` lines in robots.txt.
        Returns the first non-empty result.
        """
        candidates: list[str] = []
        if self._override_sitemap:
            candidates.append(self._override_sitemap)
        else:
            candidates.append(f"{self._root}/sitemap.xml")
            candidates.append(f"{self._root}/sitemap_index.xml")

        for url in candidates:
            urls = await self._walk_sitemap(client, url)
            if urls:
                return urls

        # robots.txt fallback
        robots = await self._fetch_text(client, f"{self._root}/robots.txt")
        if robots:
            for line in robots.splitlines():
                m = re.match(r"^\s*Sitemap:\s*(\S+)\s*$", line, re.IGNORECASE)
                if not m:
                    continue
                urls = await self._walk_sitemap(client, m.group(1).strip())
                if urls:
                    return urls
        return []

    async def _walk_sitemap(
        self,
        client,
        sitemap_url: str,
        *,
        depth: int = 0,
        budget: int | None = None,
    ) -> list[str]:
        """Fetch and parse one sitemap URL into page URLs.

        Sitemap-index files are recursed up to two levels deep. When
        ``budget`` is set the walker short-circuits as soon as enough
        URLs are collected — critical for mega-indexes like
        ``docs.aws.amazon.com`` (thousands of per-guide sitemaps) and
        ``hexdocs.pm`` (one sitemap per Hex package) that otherwise
        spend hours enumerating leaves we'll discard.
        """
        import xml.etree.ElementTree as ET

        if budget is None:
            budget = self._max_pages

        xml = await self._fetch_xml(client, sitemap_url)
        if xml is None:
            return []
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            logger.warning("sitemap %s: invalid xml at %s", self._root, sitemap_url)
            return []

        tag = root.tag.split("}", 1)[-1]
        out: list[str] = []
        # NB: ElementTree's Element is falsy when it has no child
        # elements (e.g. ``<loc>...</loc>`` carries text but no
        # children). Using ``a or b`` on Elements silently picks ``b``
        # for childless tags — which broke our first sitemap discovery
        # (all docs sites returned zero URLs). Explicit ``is None``
        # checks are the safe form.
        if tag == "urlset":
            url_els = root.findall(f"{_SM_NS}url")
            if not url_els:
                url_els = root.findall("url")
            for url_el in url_els:
                loc_el = url_el.find(f"{_SM_NS}loc")
                if loc_el is None:
                    loc_el = url_el.find("loc")
                if loc_el is not None and loc_el.text:
                    out.append(loc_el.text.strip())
                    if len(out) >= budget:
                        return out
        elif tag == "sitemapindex" and depth < 2:
            sm_els = root.findall(f"{_SM_NS}sitemap")
            if not sm_els:
                sm_els = root.findall("sitemap")
            for sm_el in sm_els:
                loc_el = sm_el.find(f"{_SM_NS}loc")
                if loc_el is None:
                    loc_el = sm_el.find("loc")
                if loc_el is not None and loc_el.text:
                    remaining = budget - len(out)
                    if remaining <= 0:
                        return out
                    out.extend(
                        await self._walk_sitemap(
                            client,
                            loc_el.text.strip(),
                            depth=depth + 1,
                            budget=remaining,
                        )
                    )
                    if len(out) >= budget:
                        return out
        return out

    async def _fetch_xml(self, client, url: str) -> str | None:
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None
            body = resp.content
            ctype = resp.headers.get("content-type", "")
            if (
                url.endswith(".gz")
                or ctype.startswith("application/x-gzip")
                or ctype.startswith("application/gzip")
            ):
                body = gzip.decompress(body)
            return body.decode("utf-8", errors="replace")
        except Exception:
            return None

    async def _fetch_text(self, client, url: str) -> str | None:
        try:
            resp = await client.get(url)
            return resp.text if resp.status_code == 200 else None
        except Exception:
            return None

    # ----- URL filtering --------------------------------------------

    def _filter_and_cap(self, urls: Iterable[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for u in urls:
            if u in seen:
                continue
            seen.add(u)
            if self._include_paths:
                path = urlparse(u).path
                if not any(path.startswith(p) for p in self._include_paths):
                    continue
            out.append(u)
            if len(out) >= self._max_pages:
                break
        return out

    # ----- extraction (single page) ---------------------------------

    def _extract(self, url: str) -> SourceDocument | None:
        import trafilatura

        try:
            raw = trafilatura.fetch_url(url)
        except Exception:
            self._log_failure(url, "fetch-error")
            return None
        if not raw:
            self._log_failure(url, "empty-body")
            return None
        text = trafilatura.extract(
            raw,
            include_comments=False,
            favor_precision=True,
            output_format="txt",
        )
        if not text or len(text.strip()) < 100:
            self._log_failure(url, "short-extract")
            return None
        meta = trafilatura.extract_metadata(raw)
        title = meta.title if meta and meta.title else url
        return SourceDocument(
            source_id=url,
            title=str(title),
            body=text,
            url=url,
            reference_time=None,
            metadata={
                "url": url,
                "title": str(title),
                "extractor": "trafilatura-sitemap",
                "domain": self._domain,
            },
        )

    # ----- failure log ----------------------------------------------

    def _log_failure(self, url: str, reason: str) -> None:
        """Append one JSONL line to the failed-URLs log.

        Best-effort — never crash the connector because logging
        misbehaved; emit a warning instead.
        """
        try:
            path = Path(get_settings().lighthouse_failed_urls_log)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "url": url,
                            "reason": reason,
                            "source_root": self._root,
                            "ts": datetime.now(UTC).isoformat(),
                        }
                    )
                    + "\n"
                )
        except Exception:
            logger.exception(
                "could not write failed-url log for %s (reason=%s)", url, reason
            )
