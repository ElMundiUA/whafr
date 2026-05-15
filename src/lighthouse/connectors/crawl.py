"""Whole-site crawl connector backed by Firecrawl.

For "trusted canonical" sources where it makes more sense to grab the
entire domain or subtree than to hand-pick URLs. Examples: Anthropic
docs, Linear docs, Martin Fowler's bliki, PostgreSQL official docs.

Why Firecrawl rather than rolling our own crawler?

- **SPAs work out of the box.** Linear docs, Notion-rendered sites,
  modern doc generators emit empty HTML and hydrate client-side.
  Firecrawl runs a real browser; we don't have to operate one.
- **Robots.txt + rate-limiting + retries** are handled — we'd build
  these wrong if we wrote them ourselves.
- **Content extraction is already Article-aware** — boilerplate gets
  stripped at crawl time, so we don't double-extract.

What this connector is *not* for:

- **Aggregator / mixed-quality sites** (Medium, Hacker News, Reddit).
  Whole-site crawl on these floods the graph with noise. Keep
  surgical URL lists in ``WebConnector`` for those.
- **Sites behind login walls.** Firecrawl can't auth as you. Skip.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence

from lighthouse.connectors.base import Connector, SourceDocument
from lighthouse.core.config import get_settings

logger = logging.getLogger(__name__)


class CrawlConnector(Connector):
    name = "crawl"

    def __init__(
        self,
        root: str,
        *,
        include_paths: Sequence[str] | None = None,
        max_pages: int = 500,
        only_main_content: bool = True,
        wait_for_dynamic: int = 0,
    ) -> None:
        """
        Args:
            root: domain root, e.g. ``https://martinfowler.com``
            include_paths: optional list of URL-path prefixes the crawl
                must stay within. Example: ``["/bliki/", "/articles/"]``
                limits crawl to those subtrees of ``root``. If absent
                Firecrawl crawls everything reachable from root.
            max_pages: hard cap on documents emitted. Bound the cost.
            only_main_content: if True, ask Firecrawl to strip nav /
                footer / sidebars before returning the body.
            wait_for_dynamic: milliseconds Firecrawl waits for SPA
                hydration before grabbing the DOM. 0 = static parse.
        """
        self._root = root.rstrip("/")
        self._include_paths = list(include_paths or [])
        self._max_pages = max_pages
        self._only_main_content = only_main_content
        self._wait_for_dynamic = wait_for_dynamic

    async def ingest(self) -> AsyncIterator[SourceDocument]:
        settings = get_settings()
        if not settings.firecrawl_api_key:
            logger.warning(
                "CrawlConnector for %s skipped — FIRECRAWL_API_KEY is empty",
                self._root,
            )
            return

        # Lazy import — httpx is core dep but kept lazy so connectors
        # that don't need it (markdown) skip the import cost.
        import httpx

        base = settings.firecrawl_base_url.rstrip("/")
        api_key = settings.firecrawl_api_key
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        payload: dict = {
            "url": self._root,
            "limit": self._max_pages,
            "scrapeOptions": {
                "formats": ["markdown"],
                "onlyMainContent": self._only_main_content,
            },
        }
        if self._include_paths:
            # Firecrawl's `includePaths` are glob-ish path patterns
            # rooted at the host. Convert ``"/bliki/"`` → ``"bliki/.*"``
            # so the regex matches sub-pages.
            payload["includePaths"] = [
                self._normalize_include(p) for p in self._include_paths
            ]
        if self._wait_for_dynamic:
            payload["scrapeOptions"]["waitFor"] = self._wait_for_dynamic

        # Firecrawl's /v1/crawl is async — we POST to start a job, then
        # poll the returned job id until "completed". For ingest-time
        # use a generous timeout: large doc trees can take minutes.
        async with httpx.AsyncClient(timeout=600.0, headers=headers) as client:
            try:
                start = await client.post(f"{base}/v1/crawl", json=payload)
                start.raise_for_status()
            except httpx.HTTPError:
                logger.exception("firecrawl start failed for %s", self._root)
                return
            job = start.json()
            job_id = job.get("id") or job.get("jobId")
            if not job_id:
                logger.error("firecrawl returned no job id for %s: %s", self._root, job)
                return
            logger.info("firecrawl job %s started for %s", job_id, self._root)

            # Poll until done. Firecrawl exposes /v1/crawl/<id> which
            # paginates results — we drain pages as they become
            # available rather than waiting for the whole job.
            import asyncio

            next_url = f"{base}/v1/crawl/{job_id}"
            seen_urls: set[str] = set()
            total_yielded = 0
            while next_url and total_yielded < self._max_pages:
                try:
                    poll = await client.get(next_url)
                    poll.raise_for_status()
                except httpx.HTTPError:
                    logger.exception("firecrawl poll failed for job %s", job_id)
                    return
                page = poll.json()
                data = page.get("data") or []
                for item in data:
                    md = (item.get("markdown") or "").strip()
                    if len(md) < 100:
                        continue
                    item_meta = item.get("metadata") or {}
                    url = item_meta.get("sourceURL") or item_meta.get("url") or ""
                    if not url or url in seen_urls:
                        continue
                    seen_urls.add(url)
                    yield SourceDocument(
                        source_id=url,
                        title=str(item_meta.get("title") or url),
                        body=md,
                        url=url,
                        reference_time=None,
                        metadata={
                            "url": url,
                            "title": str(item_meta.get("title") or ""),
                            "extractor": "firecrawl",
                            "crawl_root": self._root,
                        },
                    )
                    total_yielded += 1
                    if total_yielded >= self._max_pages:
                        break

                status = page.get("status")
                if status == "completed":
                    return
                next_url = page.get("next")
                if not next_url:
                    # Still running; back off and re-poll the same id.
                    await asyncio.sleep(3.0)
                    next_url = f"{base}/v1/crawl/{job_id}"

    # ----- helpers ---------------------------------------------------

    @staticmethod
    def _normalize_include(path: str) -> str:
        """Make a human-friendly path prefix into a Firecrawl regex.

        ``"/bliki/"`` → ``"^/bliki/.*"`` so the matcher anchors at the
        start of the URL path and includes everything underneath.
        """
        anchored = path if path.startswith("/") else "/" + path
        if not anchored.endswith("/"):
            anchored += "/"
        return f"^{anchored}.*"
