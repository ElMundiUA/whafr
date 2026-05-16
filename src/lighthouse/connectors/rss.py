"""RSS / Atom feed connector.

Subscribes to a feed URL and yields one ``SourceDocument`` per entry.
Targeted at corporate engineering blogs, release notes, and CVE feeds —
the time-sensitive content frontier models *can't have* because it
post-dates their training cutoff. See E22 in the product plan.

Why no ``feedparser`` dependency: feedparser is an excellent library
but adds a heavy transitive footprint (sgmlop, chardet). RSS 2.0 and
Atom 1.0 are both straightforward XML; we parse them with the stdlib.
We accept loose interpretation (missing namespaces, mixed shapes) the
same way browsers do.

Each entry's body is taken from the feed itself when it carries full
content (``content:encoded`` for RSS, ``<content>`` for Atom). When
the feed only provides a summary/link, we follow the link and run
trafilatura on the article body — this matches what humans do when
they click through. Entries with neither feed-content nor
trafilatura-extractable body are skipped (logged, never crashed).

Authentication is not modeled — public feeds only. If a feed needs
auth, drop a token in the URL or wrap it behind a proxy.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator, Iterable
from datetime import datetime
from urllib.parse import urlparse

from lighthouse.connectors.base import (
    Connector,
    SourceDocument,
    parse_publish_date,
)

logger = logging.getLogger(__name__)


# Common namespaces seen in real-world feeds.
_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"
_DC_NS = "{http://purl.org/dc/elements/1.1/}"


class RssConnector(Connector):
    """Pull entries from an RSS 2.0 or Atom 1.0 feed."""

    name = "rss"

    def __init__(
        self,
        feeds: Iterable[str],
        *,
        max_entries: int = 50,
        fetch_body_when_missing: bool = True,
        min_body_chars: int = 200,
    ) -> None:
        """
        Args:
            feeds: One or more feed URLs. Each is parsed independently;
                a 404 / parse error on one doesn't abort the others.
            max_entries: Per-feed cap on entries emitted. Most blogs
                carry 10-20 entries in their feed at any moment;
                higher caps just make re-runs slower without surfacing
                more content (the feed wouldn't carry it).
            fetch_body_when_missing: When True and a feed entry has
                only a link + summary, follow the link and extract
                with trafilatura. Slower but recovers the canonical
                article body — usually what callers want for blog
                content. Disable for CVE feeds where the summary IS
                the canonical text.
            min_body_chars: Entries whose final body falls below this
                are skipped (logged). Stops noise like "subscribe to
                read more" stubs from polluting the graph.
        """
        self._feeds = list(feeds)
        self._max_entries = max_entries
        self._fetch_body = fetch_body_when_missing
        self._min_body = min_body_chars

    async def ingest(self) -> AsyncIterator[SourceDocument]:
        if not self._feeds:
            logger.warning("rss connector got no feed URLs — nothing to do")
            return
        import httpx

        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": "lighthouse-rss/0.1"},
        ) as client:
            for feed_url in self._feeds:
                try:
                    async for doc in self._ingest_feed(client, feed_url):
                        yield doc
                except Exception:
                    logger.exception("rss feed %s failed — continuing", feed_url)
                    continue

    # ----- per-feed -------------------------------------------------------

    async def _ingest_feed(
        self, client, feed_url: str
    ) -> AsyncIterator[SourceDocument]:
        try:
            resp = await client.get(feed_url)
            resp.raise_for_status()
        except Exception:
            logger.exception("rss feed %s GET failed", feed_url)
            return

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError:
            logger.warning("rss feed %s — invalid XML, skipping", feed_url)
            return

        tag = root.tag.split("}", 1)[-1]
        if tag == "rss":
            entries = self._parse_rss(root)
        elif tag == "feed":
            entries = self._parse_atom(root)
        else:
            logger.warning(
                "rss feed %s — unrecognised root tag %r, skipping",
                feed_url,
                tag,
            )
            return

        domain = urlparse(feed_url).netloc
        emitted = 0
        for entry in entries:
            if emitted >= self._max_entries:
                break
            doc = await self._materialise_entry(client, entry, feed_url, domain)
            if doc is not None:
                emitted += 1
                yield doc

    # ----- RSS 2.0 --------------------------------------------------------

    def _parse_rss(self, root: ET.Element) -> list[dict]:
        out: list[dict] = []
        for item in root.iter("item"):
            entry = {
                "title": self._text(item, "title"),
                "link": self._text(item, "link"),
                "guid": self._text(item, "guid"),
                "summary": self._text(item, "description"),
                "content": self._text(item, f"{_CONTENT_NS}encoded"),
                "date_raw": (
                    self._text(item, "pubDate")
                    or self._text(item, f"{_DC_NS}date")
                ),
            }
            out.append(entry)
        return out

    # ----- Atom 1.0 -------------------------------------------------------

    def _parse_atom(self, root: ET.Element) -> list[dict]:
        out: list[dict] = []
        for entry_el in root.iter(f"{_ATOM_NS}entry"):
            # Atom links can carry rel=alternate (the article URL) or
            # rel=self (the feed entry endpoint). We want alternate.
            link = ""
            for link_el in entry_el.findall(f"{_ATOM_NS}link"):
                rel = link_el.attrib.get("rel", "alternate")
                if rel == "alternate":
                    link = link_el.attrib.get("href", "")
                    break
            entry = {
                "title": self._text(entry_el, f"{_ATOM_NS}title"),
                "link": link,
                "guid": self._text(entry_el, f"{_ATOM_NS}id"),
                "summary": self._text(entry_el, f"{_ATOM_NS}summary"),
                "content": self._text(entry_el, f"{_ATOM_NS}content"),
                "date_raw": (
                    self._text(entry_el, f"{_ATOM_NS}published")
                    or self._text(entry_el, f"{_ATOM_NS}updated")
                ),
            }
            out.append(entry)
        return out

    # ----- materialise one entry -----------------------------------------

    async def _materialise_entry(
        self, client, entry: dict, feed_url: str, domain: str
    ) -> SourceDocument | None:
        link = entry.get("link") or ""
        title = entry.get("title") or link or "(untitled)"
        # Prefer the feed-carried body; some blogs publish full content
        # in the feed, others publish a stub + link. content:encoded
        # commonly carries HTML — strip tags with a quick regex pass
        # before storing.
        body = entry.get("content") or entry.get("summary") or ""
        body = self._strip_html(body)
        if (not body or len(body) < self._min_body) and link and self._fetch_body:
            body = await self._fetch_article_body(client, link)
        if not body or len(body) < self._min_body:
            logger.debug(
                "rss entry %r skipped — body %d chars below %d",
                link or title,
                len(body),
                self._min_body,
            )
            return None

        ref: datetime | None = parse_publish_date(entry.get("date_raw"))
        source_id = entry.get("guid") or link or f"rss:{feed_url}:{title}"
        return SourceDocument(
            source_id=str(source_id),
            title=str(title),
            body=body,
            url=link or feed_url,
            reference_time=ref,
            metadata={
                "feed_url": feed_url,
                "domain": domain,
                "title": str(title),
                "extractor": "rss",
                "date": entry.get("date_raw") or "",
            },
        )

    async def _fetch_article_body(self, client, url: str) -> str:
        """Follow a feed link and run trafilatura on the article body."""
        import trafilatura

        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                return ""
            raw = resp.text
        except Exception:
            return ""
        if not raw:
            return ""
        text = trafilatura.extract(
            raw,
            include_comments=False,
            favor_precision=True,
            output_format="txt",
        )
        return text or ""

    # ----- helpers --------------------------------------------------------

    @staticmethod
    def _text(el: ET.Element, tag: str) -> str:
        found = el.find(tag)
        if found is None or found.text is None:
            return ""
        return found.text.strip()

    @staticmethod
    def _strip_html(s: str) -> str:
        """Cheap-but-correct HTML strip for feed bodies. Real-world
        feed content uses <p>, <a>, <code> etc. — trafilatura would
        give better extraction but is overkill here; the feed already
        decided this is the article body."""
        if not s:
            return ""
        import re as _re

        # Drop scripts/styles outright then drop remaining tags.
        s = _re.sub(r"<(script|style)[^>]*>.*?</\1>", "", s, flags=_re.DOTALL | _re.IGNORECASE)
        s = _re.sub(r"<[^>]+>", "", s)
        # Collapse whitespace.
        s = _re.sub(r"\s+\n", "\n", s)
        s = _re.sub(r"\n{3,}", "\n\n", s)
        return s.strip()
