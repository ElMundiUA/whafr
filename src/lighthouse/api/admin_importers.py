"""Admin REST surface for managing importers.

Mounted under `/admin/importers`. Endpoints:

- `GET    /types`         → all registered importer types + their JSON schemas
- `GET    /`              → list saved importers (no secrets)
- `POST   /`              → create
- `GET    /{id}`          → detail (config visible, secrets opaque)
- `PATCH  /{id}`          → patch fields (incl. rotated secrets)
- `DELETE /{id}`          → delete
- `POST   /{id}/run`      → trigger a run (background)
- `GET    /{id}/runs`     → recent run history

Auth: the engine deployment is expected to put this router behind a
network policy or a reverse-proxy with admin auth. Same posture as
the existing `/v1/propose` endpoint (env-keyed shared secret); we
expose an optional `LIGHTHOUSE_ADMIN_TOKEN` Bearer check below.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Annotated, Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from lighthouse.api.dependencies import get_pg_pool
from lighthouse.importers import crypto, runner, store
from lighthouse.importers.registry import list_importers, lookup_importer

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/importers", tags=["admin", "importers"])

# Strong references to fire-and-forget runner tasks. Without holding
# them somewhere Python may GC the Task while it's still running.
_INFLIGHT: set[asyncio.Task[Any]] = set()


def _spawn_run(pool: asyncpg.Pool, importer_id: UUID) -> None:
    async def _go() -> None:
        try:
            await runner.run_importer(
                pool, importer_id, triggered_by="admin-ui"
            )
        except Exception:
            logger.exception("importer run failed: %s", importer_id)

    task = asyncio.create_task(_go(), name=f"import-{importer_id}")
    _INFLIGHT.add(task)
    task.add_done_callback(_INFLIGHT.discard)


# ────────────────────────── Auth ──────────────────────────

def _require_admin(
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Optional Bearer-token guard. If LIGHTHOUSE_ADMIN_TOKEN is unset
    the endpoint is open (engine deployments behind a private network
    policy don't need belt-and-braces auth). If set, every request
    must carry `Authorization: Bearer <token>`."""
    expected = os.environ.get("LIGHTHOUSE_ADMIN_TOKEN")
    if not expected:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="admin token required")
    if authorization.removeprefix("Bearer ").strip() != expected:
        raise HTTPException(status_code=401, detail="bad admin token")


# ────────────────────────── Schemas ──────────────────────────


class ImporterTypeOut(BaseModel):
    type: str
    display_name: str
    description: str
    config_schema: dict[str, Any]
    secret_keys: list[str]
    supports_discovery: bool
    discovery_required: list[str]


class DiscoveredItemOut(BaseModel):
    id: str
    name: str
    kind: str
    hint: str | None
    config_patch: dict[str, Any]


class DiscoverIn(BaseModel):
    type: str
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)


class DiscoverOut(BaseModel):
    items: list[DiscoveredItemOut]


class ImporterOut(BaseModel):
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


class ImporterCreate(BaseModel):
    type: str
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    recipe: str = Field(min_length=1, max_length=200)
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)


class ImporterUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    recipe: str | None = Field(default=None, min_length=1, max_length=200)
    config: dict[str, Any] | None = None
    secrets: dict[str, str] | None = None
    enabled: bool | None = None


class RunOut(BaseModel):
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


class RunQueuedOut(BaseModel):
    run_id: UUID
    importer_id: UUID
    status: str = "queued"


# ────────────────────────── Mapping helpers ──────────────────────────


def _row_to_out(row: store.ImporterRow) -> ImporterOut:
    return ImporterOut(
        id=row.id,
        type=row.type,
        name=row.name,
        description=row.description,
        recipe=row.recipe,
        config=row.config,
        has_secrets=row.secrets_enc is not None,
        enabled=row.enabled,
        status=row.status,
        last_run_at=row.last_run_at.isoformat() if row.last_run_at else None,
        last_error=row.last_error,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def _run_to_out(r: store.RunRow) -> RunOut:
    return RunOut(
        id=r.id,
        importer_id=r.importer_id,
        started_at=r.started_at.isoformat(),
        finished_at=r.finished_at.isoformat() if r.finished_at else None,
        status=r.status,
        items_total=r.items_total,
        items_done=r.items_done,
        chunks_added=r.chunks_added,
        error_text=r.error_text,
        triggered_by=r.triggered_by,
    )


def _filter_secrets(
    type_key: str,
    raw: dict[str, str],
) -> dict[str, str]:
    """Drop any keys the importer type didn't declare as secret —
    everything else belongs in plain config."""
    cls = lookup_importer(type_key)
    allowed = set(cls.meta.secret_keys)
    return {k: v for k, v in raw.items() if k in allowed and v}


# ────────────────────────── Routes ──────────────────────────


@router.get(
    "/types",
    response_model=list[ImporterTypeOut],
    dependencies=[Depends(_require_admin)],
)
async def list_types() -> list[ImporterTypeOut]:
    """All importer types the registry knows about."""
    return [
        ImporterTypeOut(
            type=cls.meta.type,
            display_name=cls.meta.display_name,
            description=cls.meta.description,
            config_schema=cls.meta.config_schema,
            secret_keys=list(cls.meta.secret_keys),
            supports_discovery=cls.supports_discovery,
            discovery_required=list(cls.meta.discovery_required),
        )
        for cls in list_importers()
    ]


@router.post(
    "/discover",
    response_model=DiscoverOut,
    dependencies=[Depends(_require_admin)],
)
async def discover_route(body: DiscoverIn) -> DiscoverOut:
    """Probe the source with provided creds and list available items.

    No DB writes — secrets stay in memory only. The wizard sends them
    again on save (where they get encrypted)."""
    try:
        cls = lookup_importer(body.type)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not cls.supports_discovery:
        raise HTTPException(
            status_code=400, detail=f"{body.type} does not support discovery"
        )
    importer = cls()
    try:
        items = await asyncio.to_thread(
            importer.discover, body.config, body.secrets
        )
    except NotImplementedError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("discover failed for %s", body.type)
        raise HTTPException(
            status_code=502, detail=f"discover failed: {exc}"
        ) from exc
    return DiscoverOut(
        items=[
            DiscoveredItemOut(
                id=i.id,
                name=i.name,
                kind=i.kind,
                hint=i.hint,
                config_patch=i.config_patch,
            )
            for i in items
        ]
    )


@router.get(
    "/",
    response_model=list[ImporterOut],
    dependencies=[Depends(_require_admin)],
)
async def list_route(
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
) -> list[ImporterOut]:
    async with pool.acquire() as conn:
        rows = await store.list_all(conn)
    return [_row_to_out(r) for r in rows]


@router.post(
    "/",
    response_model=ImporterOut,
    dependencies=[Depends(_require_admin)],
)
async def create_route(
    body: ImporterCreate,
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
) -> ImporterOut:
    try:
        lookup_importer(body.type)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    secret_payload = _filter_secrets(body.type, body.secrets)
    secrets_enc: bytes | None = None
    if secret_payload:
        try:
            secrets_enc = crypto.encrypt_secrets(secret_payload)
        except crypto.MissingMasterKeyError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    async with pool.acquire() as conn:
        row = await store.create(
            conn,
            type_=body.type,
            name=body.name,
            description=body.description,
            recipe=body.recipe,
            config=body.config,
            secrets_enc=secrets_enc,
            created_by=None,
        )
    return _row_to_out(row)


@router.get(
    "/{importer_id}",
    response_model=ImporterOut,
    dependencies=[Depends(_require_admin)],
)
async def get_route(
    importer_id: UUID,
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
) -> ImporterOut:
    async with pool.acquire() as conn:
        row = await store.get(conn, importer_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return _row_to_out(row)


@router.patch(
    "/{importer_id}",
    response_model=ImporterOut,
    dependencies=[Depends(_require_admin)],
)
async def update_route(
    importer_id: UUID,
    body: ImporterUpdate,
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
) -> ImporterOut:
    secrets_enc: bytes | None = None
    keep_secrets = True
    if body.secrets is not None:
        # Caller sent a secrets dict — they intend to rotate. Empty
        # dict means "clear all secrets"; we honour that.
        async with pool.acquire() as conn:
            existing = await store.get(conn, importer_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="not found")
        keep_secrets = False
        secret_payload = _filter_secrets(existing.type, body.secrets)
        if secret_payload:
            try:
                secrets_enc = crypto.encrypt_secrets(secret_payload)
            except crypto.MissingMasterKeyError as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc

    async with pool.acquire() as conn:
        row = await store.update(
            conn,
            importer_id,
            name=body.name,
            description=body.description,
            recipe=body.recipe,
            config=body.config,
            secrets_enc=secrets_enc,
            keep_secrets=keep_secrets,
            enabled=body.enabled,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return _row_to_out(row)


@router.delete(
    "/{importer_id}",
    status_code=204,
    dependencies=[Depends(_require_admin)],
)
async def delete_route(
    importer_id: UUID,
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
) -> None:
    async with pool.acquire() as conn:
        ok = await store.delete(conn, importer_id)
    if not ok:
        raise HTTPException(status_code=404, detail="not found")


@router.post(
    "/{importer_id}/run",
    response_model=RunQueuedOut,
    dependencies=[Depends(_require_admin)],
)
async def run_route(
    importer_id: UUID,
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
) -> RunQueuedOut:
    """Kick off a run in the background. Returns immediately with the
    importer id; poll `/{id}/runs` for the actual run row's progress.

    We instantiate the importer up-front (without running it) so a
    missing optional-dep package fails the request with a 422 +
    pip-install hint instead of letting the background task swallow
    the error into the run row."""
    async with pool.acquire() as conn:
        row = await store.get(conn, importer_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    if row.status == "running":
        raise HTTPException(status_code=409, detail="already running")
    try:
        lookup_importer(row.type)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _spawn_run(pool, importer_id)
    return RunQueuedOut(run_id=importer_id, importer_id=importer_id)


@router.get(
    "/{importer_id}/runs",
    response_model=list[RunOut],
    dependencies=[Depends(_require_admin)],
)
async def runs_route(
    importer_id: UUID,
    pool: Annotated[asyncpg.Pool, Depends(get_pg_pool)],
    limit: int = 20,
) -> list[RunOut]:
    async with pool.acquire() as conn:
        rows = await store.recent_runs(conn, importer_id, limit=limit)
    return [_run_to_out(r) for r in rows]
