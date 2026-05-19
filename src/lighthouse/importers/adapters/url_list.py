"""URL-list importer.

Smallest possible importer: paste a list of URLs (one per line, or
comma-separated), the runner pipes each through `WebConnector` which
extracts article body via trafilatura (or docling-serve for PDFs).
Useful for "I just want these 12 RFCs indexed."
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from lighthouse.connectors.base import Connector
from lighthouse.connectors.web import WebConnector
from lighthouse.importers.base import ImporterMeta, LighthouseImporter
from lighthouse.importers.registry import register

# Accept newlines, commas, or whitespace between URLs.
_URL_SEPARATORS = re.compile(r"[\s,]+")


@register
class UrlListImporter(LighthouseImporter):
    meta = ImporterMeta(
        type="url_list",
        display_name="URL list",
        description=(
            "Paste a list of URLs (HTML or PDF). Each one is fetched, "
            "stripped of boilerplate, and added to the corpus."
        ),
        config_schema={
            "type": "object",
            "required": ["urls"],
            "properties": {
                "urls": {
                    "type": "string",
                    "title": "URLs",
                    "description": "One URL per line (or comma-separated).",
                    "format": "textarea",
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
        raw = str(config.get("urls", "")).strip()
        urls = [u for u in _URL_SEPARATORS.split(raw) if u]
        return WebConnector(urls)
