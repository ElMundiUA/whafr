"""Wire types — mirrors the server's pydantic models 1:1.

Hand-maintained: when the engine adds a field, add it here and bump
the client's minor version. Annotated as `BaseModel` (not TypedDict)
so callers get attribute access + validation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


# ────────────────────────── Search / fetch ──────────────────────────


class SearchHit(BaseModel):
    node_id: str
    summary: str
    source: str | None = None
    episode_ids: list[str] = Field(default_factory=list)
    valid_from: str | None = None


class SearchResponse(BaseModel):
    hits: list[SearchHit]


class Entity(BaseModel):
    node_id: str
    name: str
    summary: str | None
    labels: list[str]
    attributes: dict[str, str]


class Source(BaseModel):
    episode_id: str
    name: str
    source: str | None
    content: str
    url: str | None = None
    valid_at: str | None = None


# ────────────────────────── Corpus ──────────────────────────


class CorpusStats(BaseModel):
    total_chunks: int
    total_sources: int
    total_recipes: int
    chunks_with_summary: int
    chunks_with_embedding: int
    last_ingest_at: datetime | None


class CorpusSource(BaseModel):
    source: str
    chunk_count: int
    recipes: list[str]
    last_ingest_at: datetime | None


# ────────────────────────── Importers ──────────────────────────


class ImporterType(BaseModel):
    type: str
    display_name: str
    description: str
    config_schema: dict[str, Any]
    secret_keys: list[str]
    supports_discovery: bool
    discovery_required: list[str]


class Importer(BaseModel):
    id: UUID
    type: str
    name: str
    description: str | None
    recipe: str
    config: dict[str, Any]
    has_secrets: bool
    enabled: bool
    status: str
    last_run_at: str | None
    last_error: str | None
    created_at: str
    updated_at: str


class ImporterRun(BaseModel):
    id: UUID
    importer_id: UUID
    started_at: str
    finished_at: str | None
    status: str
    items_total: int | None
    items_done: int
    chunks_added: int
    error_text: str | None
    triggered_by: str | None


class DiscoveredItem(BaseModel):
    id: str
    name: str
    kind: str
    hint: str | None
    config_patch: dict[str, Any]


# ────────────────────────── Webhooks ──────────────────────────


class Webhook(BaseModel):
    id: UUID
    url: str
    events: list[str]
    enabled: bool
    description: str | None
    created_at: datetime
    last_delivery_at: datetime | None
    last_status: int | None
    last_error: str | None


class WebhookCreated(Webhook):
    secret: str


class WebhookDelivery(BaseModel):
    id: UUID
    webhook_id: UUID
    event: str
    status: str
    attempts: int
    next_attempt_at: datetime
    last_status: int | None
    last_error: str | None
    created_at: datetime
    delivered_at: datetime | None
