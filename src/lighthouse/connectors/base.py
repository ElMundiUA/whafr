"""Source-connector protocol.

Every connector — markdown today, LlamaIndex-hub-backed integrations
tomorrow — exposes the same async ``ingest`` method that streams
``SourceDocument`` instances to the graph layer. Keeping connectors
behind a single protocol means the librarian and the source-runner
never branch on connector type.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


_ISO_DATE_RE = re.compile(
    r"(?P<year>\d{4})[-/]?(?P<month>\d{2})[-/]?(?P<day>\d{2})"
)


def parse_publish_date(raw: str | None) -> datetime | None:
    """Parse a publish-date string into a UTC-aware datetime.

    Accepts common shapes ingested from trafilatura / sitemap lastmod /
    GitHub commit timestamps: ``2025-06-12``, ``2025/06/12``,
    ``2026-05-15T19:43:09Z``, ``20260515``. Returns ``None`` if nothing
    that looks like a date can be extracted — better than dropping the
    document over a parse failure.

    The downstream use is the chunk's ``published_at`` and a coarse
    "post-cutoff" tag — both can tolerate day-resolution. We
    deliberately don't try to recover hour/minute when only a date is
    present; that fakes precision.
    """
    if not raw:
        return None
    raw = raw.strip()
    # RFC 822 (common in RSS pubDate) — try stdlib helper first; it
    # handles the various ", DD Mmm YYYY HH:MM:SS +0000" variants.
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(raw)
        if dt is not None:
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        pass
    # ISO-with-time next (most precise after RFC 822).
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%d %H:%M:%S",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        except ValueError:
            continue
    # Then bare date — match year/month/day anywhere in the string so
    # ``Published 2025-06-12`` works.
    m = _ISO_DATE_RE.search(raw)
    if m:
        try:
            return datetime(
                int(m["year"]), int(m["month"]), int(m["day"]), tzinfo=UTC
            )
        except ValueError:
            return None
    return None


@dataclass(slots=True)
class SourceDocument:
    """One unit of source content ready to feed into the engine.

    Connectors yield whole documents; the engine splits each body into
    chunks at ingest. ``reference_time`` is the document's natural
    timestamp (publish date for docs, commit time for code, etc.) and
    becomes the chunk's ``published_at`` for time-aware filtering.
    """

    source_id: str
    title: str
    body: str
    url: str | None = None
    reference_time: datetime | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class Connector(Protocol):
    """Minimal contract every source connector implements."""

    name: str

    async def ingest(self) -> AsyncIterator[SourceDocument]:
        """Yield documents from the source.

        Implementations should be idempotent — re-running ``ingest()``
        on an unchanged source should produce the same ``source_id``
        per document so the engine's delta-skip catches no-ops.
        """
        ...
