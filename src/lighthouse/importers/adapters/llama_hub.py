"""LlamaHub-bridge adapter helpers.

LlamaHub readers (`llama_index.readers.*`) emit `llama_index.core.Document`
instances. This module provides a thin bridge so any LlamaHub reader
can be exposed as a Lighthouse importer with minimal glue:

1. Subclass `LlamaHubImporter`, set `meta`, override
   `make_reader(config, secrets)` to instantiate the upstream reader.
2. The base supplies a `build_connector` that wraps the reader in a
   `LlamaHubDocumentConnector` — async-iterating its `load_data()`
   output, mapping each Document to our `SourceDocument`.

We don't register a *concrete* importer here — every llama-hub
reader has different config knobs and credential shapes; the
admin should add adapters per reader as they're needed. The
mechanics live here so each adapter is ~20 lines.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping
from typing import Any

from lighthouse.connectors.base import Connector, SourceDocument
from lighthouse.importers.base import LighthouseImporter

logger = logging.getLogger(__name__)


class LlamaHubDocumentConnector(Connector):
    """Adapt a llama-index `BaseReader` into our `Connector` Protocol.

    The reader's `load_data()` is called synchronously (most upstream
    readers are sync); we offload to a thread to keep the event loop
    free. Each upstream Document becomes one `SourceDocument`.
    """

    def __init__(
        self,
        name: str,
        reader: Any,
        load_kwargs: Mapping[str, Any] | None = None,
    ) -> None:
        self.name = name
        self._reader = reader
        self._load_kwargs = dict(load_kwargs or {})

    async def ingest(self) -> AsyncIterator[SourceDocument]:
        import asyncio

        docs = await asyncio.to_thread(self._reader.load_data, **self._load_kwargs)
        for d in docs:
            meta = getattr(d, "metadata", None) or {}
            url = meta.get("url") or meta.get("source") or meta.get("file_path")
            title = (
                meta.get("title")
                or meta.get("file_name")
                or (url.split("/")[-1] if isinstance(url, str) else None)
                or "untitled"
            )
            body = getattr(d, "text", None) or ""
            if not body.strip():
                continue
            yield SourceDocument(
                source_id=str(url or getattr(d, "id_", "") or title),
                title=str(title),
                body=body,
                url=str(url) if url else None,
                metadata={
                    k: str(v)
                    for k, v in meta.items()
                    if v is not None and not isinstance(v, (list, dict))
                },
            )


class LlamaHubImporter(LighthouseImporter):
    """Base class for adapters that wrap a single llama-hub reader.

    Subclasses set `meta` and implement `make_reader`. `connector_name`
    is the value stamped on `Connector.name` (defaults to the type key).
    """

    connector_name: str | None = None

    def make_reader(
        self,
        config: Mapping[str, Any],
        secrets: Mapping[str, str],
    ) -> Any:
        raise NotImplementedError

    def load_kwargs(
        self,
        config: Mapping[str, Any],
        secrets: Mapping[str, str],
    ) -> Mapping[str, Any]:
        """Optional kwargs forwarded to `reader.load_data(**kwargs)`."""
        return {}

    def build_connector(
        self,
        config: Mapping[str, Any],
        secrets: Mapping[str, str],
    ) -> Connector:
        reader = self.make_reader(config, secrets)
        return LlamaHubDocumentConnector(
            name=self.connector_name or self.meta.type,
            reader=reader,
            load_kwargs=self.load_kwargs(config, secrets),
        )
