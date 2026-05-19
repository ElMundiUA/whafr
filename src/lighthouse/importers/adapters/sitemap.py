"""Sitemap-driven crawl importer.

Wraps `SitemapCrawlConnector` — point at a docs site root, the
connector enumerates `sitemap.xml`, optionally filters by path
prefix, runs each URL through trafilatura. Polite per-domain
throttle. The canonical engine importer for static-doc sites.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from lighthouse.connectors.base import Connector
from lighthouse.connectors.sitemap_crawl import SitemapCrawlConnector
from lighthouse.importers.base import ImporterMeta, LighthouseImporter
from lighthouse.importers.registry import register


@register
class SitemapImporter(LighthouseImporter):
    meta = ImporterMeta(
        type="sitemap",
        display_name="Sitemap crawl",
        description=(
            "Crawl a static-doc site via its sitemap.xml. Good for "
            "framework docs, vendor reference, blog archives."
        ),
        config_schema={
            "type": "object",
            "required": ["root"],
            "properties": {
                "root": {
                    "type": "string",
                    "title": "Site root",
                    "description": "e.g. https://docs.python.org",
                    "format": "uri",
                },
                "sitemap_url": {
                    "type": "string",
                    "title": "Sitemap URL (optional)",
                    "description": (
                        "Override sitemap discovery if the file is at "
                        "a non-standard path."
                    ),
                    "format": "uri",
                },
                "include_paths": {
                    "type": "string",
                    "title": "Include path prefixes (optional)",
                    "description": "One per line, e.g. /3/library/. Empty = take everything.",
                    "format": "textarea",
                },
                "max_pages": {
                    "type": "integer",
                    "title": "Max pages",
                    "default": 200,
                    "minimum": 1,
                    "maximum": 20000,
                },
                "rate_limit_per_sec": {
                    "type": "number",
                    "title": "Rate limit (req/s)",
                    "default": 1.0,
                    "minimum": 0.1,
                    "maximum": 20.0,
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
        include_raw = str(config.get("include_paths", "") or "").strip()
        include = [p for p in include_raw.splitlines() if p.strip()] or None
        return SitemapCrawlConnector(
            root=str(config["root"]),
            sitemap_url=(str(config["sitemap_url"]) if config.get("sitemap_url") else None),
            include_paths=include,
            max_pages=int(config.get("max_pages", 200)),
            rate_limit_per_sec=float(config.get("rate_limit_per_sec", 1.0)),
        )
