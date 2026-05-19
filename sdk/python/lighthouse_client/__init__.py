"""Python client for the Lighthouse engine.

Hand-written httpx wrapper — small enough to stay readable, big
enough to cover every public surface (/v1/search, /v1/corpus,
/v1/importers, /v1/webhooks). The shape matches the FastAPI server's
pydantic models exactly so types round-trip cleanly.

Async by default (`AsyncLighthouse`); sync mirror provided
(`Lighthouse`) for scripts and tests.
"""

from __future__ import annotations

from lighthouse_client.client import AsyncLighthouse, Lighthouse, LighthouseError
from lighthouse_client.models import (
    CorpusSource,
    CorpusStats,
    DiscoveredItem,
    Importer,
    ImporterRun,
    ImporterType,
    SearchHit,
    Webhook,
    WebhookCreated,
)
from lighthouse_client.signing import verify_webhook_signature

__all__ = [
    "AsyncLighthouse",
    "Lighthouse",
    "LighthouseError",
    "CorpusSource",
    "CorpusStats",
    "DiscoveredItem",
    "Importer",
    "ImporterRun",
    "ImporterType",
    "SearchHit",
    "Webhook",
    "WebhookCreated",
    "verify_webhook_signature",
]
