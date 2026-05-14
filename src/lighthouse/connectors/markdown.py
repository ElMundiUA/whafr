"""Markdown source connector.

Reads a local directory of markdown files via LlamaIndex's
``SimpleDirectoryReader`` and emits one ``SourceDocument`` per file.
The first connector lets us prove the ingest → graph → retrieval loop
end-to-end without committing to a specific external integration.

LlamaIndex was chosen for the reader layer (rather than a hand-rolled
``Path.glob``) because the same package gives us thirty-plus other
readers (Notion, Confluence, Slack, etc.) for free — the next
connectors are LlamaHub wrappers, and using the same reader API
everywhere keeps connector code dull.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from pathlib import Path

from lighthouse.connectors.base import Connector, SourceDocument

logger = logging.getLogger(__name__)


class MarkdownConnector(Connector):
    name = "markdown"

    def __init__(self, source_dir: str | Path) -> None:
        self._source_dir = Path(source_dir)

    async def ingest(self) -> AsyncIterator[SourceDocument]:
        if not self._source_dir.exists():
            logger.warning(
                "markdown source dir %s does not exist — yielding zero docs",
                self._source_dir,
            )
            return

        # Lazy import — LlamaIndex pulls a lot of transitive deps, no need
        # to pay that cost on test runs that don't touch this connector.
        from llama_index.core import SimpleDirectoryReader

        reader = SimpleDirectoryReader(
            input_dir=str(self._source_dir),
            required_exts=[".md", ".markdown"],
            recursive=True,
        )
        for doc in reader.load_data():
            yield SourceDocument(
                source_id=str(doc.id_),
                title=doc.metadata.get("file_name") or str(doc.id_),
                body=doc.text,
                url=None,
                reference_time=None,
                metadata={
                    str(k): str(v)
                    for k, v in (doc.metadata or {}).items()
                },
            )
