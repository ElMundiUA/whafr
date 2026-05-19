"""RSS / Atom feed importer.

Wraps `RssConnector` — index entries from one or more RSS 2.0 / Atom
feeds. Useful for blogs, changelogs, release-note feeds, security
advisories. The connector falls back to fetching the full page body
via trafilatura when the feed entry only carries a summary.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from lighthouse.connectors.base import Connector
from lighthouse.connectors.rss import RssConnector
from lighthouse.importers.base import ImporterMeta, LighthouseImporter
from lighthouse.importers.registry import register

_FEED_SEPARATORS = re.compile(r"[\s,]+")


@register
class RssImporter(LighthouseImporter):
    meta = ImporterMeta(
        type="rss",
        display_name="RSS / Atom feeds",
        description=(
            "Index entries from one or more RSS 2.0 or Atom 1.0 feeds. "
            "Good for changelogs, blogs, security advisories."
        ),
        config_schema={
            "type": "object",
            "required": ["feeds"],
            "properties": {
                "feeds": {
                    "type": "string",
                    "title": "Feed URLs",
                    "description": "One per line (or comma-separated).",
                    "format": "textarea",
                },
                "max_entries": {
                    "type": "integer",
                    "title": "Max entries per feed",
                    "default": 50,
                    "minimum": 1,
                    "maximum": 1000,
                },
                "fetch_body_when_missing": {
                    "type": "boolean",
                    "title": "Fetch full article body when the feed has none",
                    "default": True,
                },
                "min_body_chars": {
                    "type": "integer",
                    "title": "Minimum body length (chars)",
                    "default": 200,
                    "minimum": 0,
                    "maximum": 10000,
                },
            },
        },
        secret_keys=(),
    )

    def build_connector(
        self,
        config: Mapping[str, Any],
        secrets: Mapping[str, str],
    ) -> Connector:
        raw = str(config.get("feeds", "")).strip()
        feeds = [u for u in _FEED_SEPARATORS.split(raw) if u]
        return RssConnector(
            feeds=feeds,
            max_entries=int(config.get("max_entries", 50)),
            fetch_body_when_missing=bool(
                config.get("fetch_body_when_missing", True)
            ),
            min_body_chars=int(config.get("min_body_chars", 200)),
        )
