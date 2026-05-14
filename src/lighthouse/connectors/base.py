"""Source-connector protocol.

Every connector — markdown today, LlamaIndex-hub-backed integrations
tomorrow — exposes the same async ``ingest`` method that streams
``SourceDocument`` instances to the graph layer. Keeping connectors
behind a single protocol means the librarian and the source-runner
never branch on connector type.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class SourceDocument:
    """One unit of source content ready to feed into Graphiti as an episode.

    Connectors are responsible for chunking — the graph layer does not
    re-split. ``reference_time`` is the document's natural timestamp
    (publish date for docs, commit time for code, etc.); Graphiti uses
    it to age out earlier conflicting facts.
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
        per document so Graphiti's dedup catches no-ops.
        """
        ...
