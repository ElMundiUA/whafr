"""Async + sync httpx wrappers.

`AsyncLighthouse` is the canonical client (matches Ship's async code).
`Lighthouse` wraps it for ad-hoc scripting where async is overkill.

All methods return validated pydantic models — pass them around
without re-parsing.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx

from lighthouse_client.models import (
    CorpusSource,
    CorpusStats,
    DiscoveredItem,
    Entity,
    Importer,
    ImporterRun,
    ImporterType,
    SearchResponse,
    Source,
    Webhook,
    WebhookCreated,
    WebhookDelivery,
)


class LighthouseError(RuntimeError):
    """Non-2xx response from the engine."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Lighthouse {status}: {body[:300]}")
        self.status = status
        self.body = body


# ────────────────────────── Async client ──────────────────────────


class AsyncLighthouse:
    """Async client. Long-lived per process — opens one httpx.AsyncClient."""

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        timeout: float = 30.0,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def __aenter__(self) -> AsyncLighthouse:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ────────── internals ──────────

    def _headers(self, body: bool) -> dict[str, str]:
        h: dict[str, str] = {}
        if body:
            h["Content-Type"] = "application/json"
        if self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any | None = None,
    ) -> Any:
        r = await self._client.request(
            method,
            self._base + path,
            params=params,
            json=body,
            headers=self._headers(body is not None),
        )
        if r.status_code >= 400:
            raise LighthouseError(r.status_code, r.text)
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    # ────────── retrieval ──────────

    async def search(
        self,
        q: str,
        *,
        top_k: int = 10,
        sort: str = "relevance",
    ) -> SearchResponse:
        data = await self._request(
            "GET", "/v1/search", params={"q": q, "top_k": top_k, "sort": sort}
        )
        return SearchResponse.model_validate(data)

    async def fetch_entity(self, node_id: str) -> Entity:
        data = await self._request("GET", f"/v1/fetch_entity/{node_id}")
        return Entity.model_validate(data)

    async def fetch_source(self, episode_id: str) -> Source:
        data = await self._request("GET", f"/v1/fetch_source/{episode_id}")
        return Source.model_validate(data)

    # ────────── corpus ──────────

    async def corpus_stats(self) -> CorpusStats:
        data = await self._request("GET", "/v1/corpus/stats")
        return CorpusStats.model_validate(data)

    async def corpus_sources(
        self, *, limit: int = 100, order: str = "chunks"
    ) -> list[CorpusSource]:
        data = await self._request(
            "GET", "/v1/corpus/sources", params={"limit": limit, "order": order}
        )
        return [CorpusSource.model_validate(r) for r in data]

    # ────────── importers ──────────

    async def importer_types(self) -> list[ImporterType]:
        data = await self._request("GET", "/v1/importers/types")
        return [ImporterType.model_validate(r) for r in data]

    async def importers(self) -> list[Importer]:
        data = await self._request("GET", "/v1/importers/")
        return [Importer.model_validate(r) for r in data]

    async def importer(self, importer_id: str | UUID) -> Importer:
        data = await self._request("GET", f"/v1/importers/{importer_id}")
        return Importer.model_validate(data)

    async def create_importer(
        self,
        *,
        type: str,
        name: str,
        recipe: str,
        config: dict[str, Any],
        description: str | None = None,
        secrets: dict[str, str] | None = None,
    ) -> Importer:
        data = await self._request(
            "POST",
            "/v1/importers/",
            body={
                "type": type,
                "name": name,
                "recipe": recipe,
                "config": config,
                "description": description,
                "secrets": secrets or {},
            },
        )
        return Importer.model_validate(data)

    async def update_importer(
        self,
        importer_id: str | UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        recipe: str | None = None,
        config: dict[str, Any] | None = None,
        secrets: dict[str, str] | None = None,
        enabled: bool | None = None,
    ) -> Importer:
        body: dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        if description is not None:
            body["description"] = description
        if recipe is not None:
            body["recipe"] = recipe
        if config is not None:
            body["config"] = config
        if secrets is not None:
            body["secrets"] = secrets
        if enabled is not None:
            body["enabled"] = enabled
        data = await self._request(
            "PATCH", f"/v1/importers/{importer_id}", body=body
        )
        return Importer.model_validate(data)

    async def delete_importer(self, importer_id: str | UUID) -> None:
        await self._request("DELETE", f"/v1/importers/{importer_id}")

    async def run_importer(
        self, importer_id: str | UUID
    ) -> dict[str, str]:
        return await self._request("POST", f"/v1/importers/{importer_id}/run")

    async def importer_runs(
        self, importer_id: str | UUID
    ) -> list[ImporterRun]:
        data = await self._request("GET", f"/v1/importers/{importer_id}/runs")
        return [ImporterRun.model_validate(r) for r in data]

    async def discover(
        self,
        *,
        type: str,
        config: dict[str, Any],
        secrets: dict[str, str],
    ) -> list[DiscoveredItem]:
        data = await self._request(
            "POST",
            "/v1/importers/discover",
            body={"type": type, "config": config, "secrets": secrets},
        )
        return [DiscoveredItem.model_validate(r) for r in data["items"]]

    # ────────── webhooks ──────────

    async def webhooks(self) -> list[Webhook]:
        data = await self._request("GET", "/v1/webhooks/")
        return [Webhook.model_validate(r) for r in data]

    async def webhook(self, webhook_id: str | UUID) -> Webhook:
        data = await self._request("GET", f"/v1/webhooks/{webhook_id}")
        return Webhook.model_validate(data)

    async def create_webhook(
        self,
        *,
        url: str,
        events: list[str] | None = None,
        description: str | None = None,
        secret: str | None = None,
        enabled: bool = True,
    ) -> WebhookCreated:
        data = await self._request(
            "POST",
            "/v1/webhooks/",
            body={
                "url": url,
                "events": events or ["*"],
                "description": description,
                "secret": secret,
                "enabled": enabled,
            },
        )
        return WebhookCreated.model_validate(data)

    async def update_webhook(
        self,
        webhook_id: str | UUID,
        *,
        url: str | None = None,
        events: list[str] | None = None,
        description: str | None = None,
        enabled: bool | None = None,
        rotate_secret: bool = False,
    ) -> Webhook | WebhookCreated:
        body: dict[str, Any] = {"rotate_secret": rotate_secret}
        if url is not None:
            body["url"] = url
        if events is not None:
            body["events"] = events
        if description is not None:
            body["description"] = description
        if enabled is not None:
            body["enabled"] = enabled
        data = await self._request(
            "PATCH", f"/v1/webhooks/{webhook_id}", body=body
        )
        # When rotate_secret, server echoes the new secret.
        if "secret" in (data or {}):
            return WebhookCreated.model_validate(data)
        return Webhook.model_validate(data)

    async def delete_webhook(self, webhook_id: str | UUID) -> None:
        await self._request("DELETE", f"/v1/webhooks/{webhook_id}")

    async def webhook_deliveries(
        self, webhook_id: str | UUID, *, limit: int = 50
    ) -> list[WebhookDelivery]:
        data = await self._request(
            "GET",
            f"/v1/webhooks/{webhook_id}/deliveries",
            params={"limit": limit},
        )
        return [WebhookDelivery.model_validate(r) for r in data]

    async def redeliver_webhook(
        self, webhook_id: str | UUID, delivery_id: str | UUID
    ) -> dict[str, str]:
        return await self._request(
            "POST",
            f"/v1/webhooks/{webhook_id}/deliveries/{delivery_id}/redeliver",
        )

    async def test_webhook(self, webhook_id: str | UUID) -> dict[str, str]:
        return await self._request("POST", f"/v1/webhooks/{webhook_id}/test")


# ────────────────────────── Sync client ──────────────────────────


class Lighthouse:
    """Sync mirror of `AsyncLighthouse` for ad-hoc scripting.

    Internally delegates to a one-shot httpx.Client; not great for
    high-throughput callers — use `AsyncLighthouse` there."""

    def __init__(
        self,
        base_url: str,
        token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._client = httpx.Client(timeout=timeout)

    def __enter__(self) -> Lighthouse:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any | None = None,
    ) -> Any:
        headers: dict[str, str] = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        r = self._client.request(
            method, self._base + path, params=params, json=body, headers=headers
        )
        if r.status_code >= 400:
            raise LighthouseError(r.status_code, r.text)
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    # The sync surface mirrors the async one but reuses the same
    # pydantic models so we don't have to write 30 methods twice —
    # delegate to a tiny dispatch table.
    def search(self, q: str, **kw: Any) -> SearchResponse:
        return SearchResponse.model_validate(
            self._request("GET", "/v1/search", params={"q": q, **kw})
        )

    def corpus_stats(self) -> CorpusStats:
        return CorpusStats.model_validate(self._request("GET", "/v1/corpus/stats"))

    def importers(self) -> list[Importer]:
        return [
            Importer.model_validate(r) for r in self._request("GET", "/v1/importers/")
        ]

    def run_importer(self, importer_id: str | UUID) -> dict[str, str]:
        return self._request("POST", f"/v1/importers/{importer_id}/run")
