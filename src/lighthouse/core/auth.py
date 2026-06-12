"""API-key auth for the retrieval surface + the shared admin guard.

Two credential kinds, deliberately separate:

- **Admin token** (``LIGHTHOUSE_ADMIN_TOKEN``) — one shared bearer for
  the instance operator. Gates every ``/v1`` admin router. Unset →
  admin requests are rejected unless ``LIGHTHOUSE_INSECURE_ADMIN=true``
  explicitly opts into open admin for local dev (the pre-0.1 behaviour
  of silently-open-when-unset was a footgun).

- **API keys** (``lh_<hex>``, table ``api_keys``) — per-workspace
  bearer credentials for retrieval (search / fetch / MCP). The
  workspace is *derived from the key*; a client-sent ``X-Workspace``
  that contradicts the key is rejected. With
  ``LIGHTHOUSE_RETRIEVAL_AUTH_REQUIRED=true`` retrieval without a
  valid key is 401; with it off (default) keyless requests fall back
  to the legacy header-or-public resolution so single-tenant and
  public-corpus deployments keep working unchanged.

Only SHA-256 hashes of key secrets are stored.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import logging
import os
import secrets as pysecrets
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from lighthouse.core.config import get_settings

logger = logging.getLogger(__name__)

KEY_PREFIX = "lh_"

# Per-workspace require_auth flags, cached so the keyless hot path costs
# one indexed SELECT per workspace per TTL instead of per request.
_WS_AUTH_TTL_SECONDS = 30.0
_ws_auth_cache: dict[str, tuple[float, bool]] = {}


def invalidate_workspace_auth_cache() -> None:
    """Called after PUT /v1/workspaces so a flag flip applies promptly
    (within one process; other replicas converge within the TTL)."""
    _ws_auth_cache.clear()


# ───────────────────────── key material ─────────────────────────


def generate_key() -> tuple[str, str]:
    """Return ``(secret, sha256_hash)``. The secret is shown to the
    caller exactly once; only the hash is persisted."""
    secret = KEY_PREFIX + pysecrets.token_hex(24)
    return secret, hash_key(secret)


def hash_key(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class ApiKey:
    id: UUID
    workspace_id: str
    name: str
    scopes: list[str]


async def lookup_key(conn: Any, secret: str) -> ApiKey | None:
    """Resolve a presented secret to its key row (None when unknown or
    revoked). Also stamps ``last_used_at`` — cheap single-row update."""
    row = await conn.fetchrow(
        """
        UPDATE api_keys SET last_used_at = NOW()
         WHERE key_hash = $1 AND revoked_at IS NULL
        RETURNING id, workspace_id, name, scopes
        """,
        hash_key(secret),
    )
    if row is None:
        return None
    return ApiKey(
        id=row["id"],
        workspace_id=row["workspace_id"],
        name=row["name"],
        scopes=list(row["scopes"] or []),
    )


# ───────────────────────── retrieval auth ─────────────────────────


@dataclass(slots=True)
class RetrievalAuth:
    """Resolved identity for one retrieval request."""

    workspace_id: str
    api_key_id: UUID | None = None


async def authenticate_retrieval(
    *,
    authorization: str | None,
    x_workspace: str | None,
    pool_factory: Any,
) -> RetrievalAuth:
    """Workspace resolution for the retrieval surface (HTTP + MCP).

    ``pool_factory`` is an async callable returning the asyncpg pool —
    passed in (rather than imported) so the MCP server and tests can
    supply their own.
    """
    from lighthouse.core.flat_graph import PUBLIC_WORKSPACE

    bearer = None
    if authorization and authorization.startswith("Bearer "):
        bearer = authorization.removeprefix("Bearer ").strip()

    if bearer and bearer.startswith(KEY_PREFIX):
        try:
            maybe = pool_factory()
            pool = await maybe if inspect.isawaitable(maybe) else maybe
            async with pool.acquire() as conn:
                key = await lookup_key(conn, bearer)
        except HTTPException:
            raise
        except Exception as exc:  # pool down ≠ auth bypass
            raise HTTPException(
                status_code=503, detail="auth backend unavailable"
            ) from exc
        if key is None:
            raise HTTPException(status_code=401, detail="invalid API key")
        if x_workspace and x_workspace != key.workspace_id:
            raise HTTPException(
                status_code=403,
                detail="X-Workspace does not match the API key's workspace",
            )
        return RetrievalAuth(workspace_id=key.workspace_id, api_key_id=key.id)

    if _retrieval_auth_required():
        raise HTTPException(
            status_code=401,
            detail="API key required (Authorization: Bearer lh_…)",
        )
    # Legacy resolution: trusted header or the public corpus — unless
    # this specific workspace opted into key-only access.
    workspace_id = x_workspace or PUBLIC_WORKSPACE
    if await _workspace_requires_auth(workspace_id, pool_factory):
        raise HTTPException(
            status_code=401,
            detail=(
                f"workspace {workspace_id!r} requires an API key "
                "(Authorization: Bearer lh_…)"
            ),
        )
    return RetrievalAuth(workspace_id=workspace_id)


async def _workspace_requires_auth(workspace_id: str, pool_factory: Any) -> bool:
    """Per-workspace require_auth flag (table `workspaces`), TTL-cached.

    Fails OPEN on lookup errors: if the DB is unreachable the search
    itself is about to fail anyway, and a missing table (pre-0010
    deployment mid-upgrade) must not lock anyone out."""
    now = time.monotonic()
    cached = _ws_auth_cache.get(workspace_id)
    if cached is not None and cached[0] > now:
        return cached[1]
    required = False
    try:
        maybe = pool_factory()
        pool = await maybe if inspect.isawaitable(maybe) else maybe
        async with pool.acquire() as conn:
            required = bool(
                await conn.fetchval(
                    "SELECT require_auth FROM workspaces WHERE id = $1",
                    workspace_id,
                )
            )
    except Exception:
        logger.debug("workspace auth-flag lookup failed", exc_info=True)
    _ws_auth_cache[workspace_id] = (now + _WS_AUTH_TTL_SECONDS, required)
    return required


def _retrieval_auth_required() -> bool:
    env = os.environ.get("LIGHTHOUSE_RETRIEVAL_AUTH_REQUIRED")
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes")
    return get_settings().lighthouse_retrieval_auth_required


# ───────────────────────── admin guard ─────────────────────────


def admin_token_configured() -> str:
    """The effective admin token ('' when unset). env beats .env so a
    container override always wins without a settings-cache refresh."""
    return (
        os.environ.get("LIGHTHOUSE_ADMIN_TOKEN")
        or get_settings().lighthouse_admin_token
    )


def insecure_admin_allowed() -> bool:
    env = os.environ.get("LIGHTHOUSE_INSECURE_ADMIN")
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes")
    return get_settings().lighthouse_insecure_admin


def check_admin(authorization: str | None) -> None:
    """Shared guard for every admin router. Raises 401 unless the
    request carries the admin bearer — or the operator explicitly
    opted into open admin (local dev)."""
    expected = admin_token_configured()
    if not expected:
        if insecure_admin_allowed():
            return
        raise HTTPException(
            status_code=401,
            detail=(
                "admin surface locked: set LIGHTHOUSE_ADMIN_TOKEN "
                "(or LIGHTHOUSE_INSECURE_ADMIN=true for local dev)"
            ),
        )
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="admin token required")
    presented = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(presented, expected):
        raise HTTPException(status_code=401, detail="bad admin token")
