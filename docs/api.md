# Lighthouse REST API

Stable API surface under `/v1/*`. Generated OpenAPI schema lives at
`/openapi.json`; interactive explorer at `/docs` (Swagger UI) and
`/redoc`.

## Auth

Two postures:

- **Public retrieval** — `/v1/search`, `/v1/fetch_entity`,
  `/v1/fetch_source` are open by default. Restrict via your ingress
  if you don't want anonymous reads.
- **Admin** — every other `/v1/*` endpoint checks the
  `Authorization: Bearer <token>` header against the
  `LIGHTHOUSE_ADMIN_TOKEN` env var. If the env var is unset, admin
  endpoints are open too (single-tenant in-cluster deployment). Set
  the var on every replica that reads the same DB.

```bash
curl -H "Authorization: Bearer $LIGHTHOUSE_ADMIN_TOKEN" \
  https://lighthouse.example.com/v1/importers/
```

## Error shape

Non-2xx responses are JSON with a `detail` field (FastAPI default):

```json
{"detail": "Importer 0123-… not found"}
```

| Status | Meaning |
|---|---|
| `400` | Bad request — unknown importer type, malformed body, etc. |
| `401` | Missing / wrong bearer (when admin token is configured) |
| `403` | Wrong scope (reserved for future per-key auth) |
| `404` | Unknown id |
| `409` | Conflict — e.g. importer is already running |
| `422` | Pydantic validation failed |
| `500` | Server bug — encryption key missing, etc. |
| `502` | Upstream provider error (Notion 5xx, etc.) |

## Endpoint catalog

### Retrieval (public)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/search?q=…&top_k=10&sort=relevance` | Hybrid BM25 + vector + rerank search. Returns ranked facts. |
| `GET` | `/v1/fetch_entity/{node_id}` | Resolve one entity (name + summary + labels). |
| `GET` | `/v1/fetch_source/{episode_id}` | Original ingested paragraph the fact was extracted from. |

`/v1/search` response:

```json
{
  "hits": [
    {
      "node_id": "…",
      "summary": "OAuth 2.0 PKCE requires a code_challenge_method of S256 …",
      "source": "https://datatracker.ietf.org/doc/html/rfc7636",
      "episode_ids": ["e1", "e2"],
      "valid_from": "2015-09-01T00:00:00Z"
    }
  ]
}
```

### Corpus introspection

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/corpus/stats` | Roll-up: total chunks/sources/recipes, coverage of summary+embedding, last ingest timestamp. |
| `GET` | `/v1/corpus/sources?limit=100&order=chunks\|recent` | Per-source roll-up: chunk count, recipes claiming it, last ingest. |

### Importers — CRUD

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/importers/types` | All registered importer types + their JSON schemas + whether they support discovery. |
| `GET` | `/v1/importers/` | List saved importer instances. |
| `POST` | `/v1/importers/` | Create. Body: `{type, name, recipe, config, secrets, description?}`. |
| `GET` | `/v1/importers/{id}` | One row. |
| `PATCH` | `/v1/importers/{id}` | Partial update. Send `secrets: {}` to clear all; omit `secrets` to keep existing. |
| `DELETE` | `/v1/importers/{id}` | Cascades to run history. |
| `POST` | `/v1/importers/{id}/run` | Trigger a run. Returns 202 + run id; poll `/runs`. |
| `GET` | `/v1/importers/{id}/runs?limit=20` | Recent run history. |
| `POST` | `/v1/importers/discover` | Probe a source with provided creds, return pickable items. Body: `{type, config, secrets}`. |

Importer row shape:

```json
{
  "id": "0123-…",
  "type": "sitemap",
  "name": "fastapi-docs",
  "description": "Migrated from developer.yaml",
  "recipe": "fastapi-docs",
  "config": {"root": "https://fastapi.tiangolo.com", "max_pages": 0},
  "has_secrets": false,
  "enabled": true,
  "status": "idle",
  "last_run_at": "2026-05-19T18:42:11Z",
  "last_error": null,
  "created_at": "2026-05-18T20:01:02Z",
  "updated_at": "2026-05-19T18:42:11Z"
}
```

`status` lifecycle: `idle → queued → running → idle | error`.

### Discovery (wizard support)

Types that set `supports_discovery: true` accept partial credentials
and probe the source for selectable items. The frontend renders only
the fields listed in `discovery_required`, then calls:

```bash
curl -X POST -H "Authorization: Bearer $TOK" \
  -H "Content-Type: application/json" \
  https://lighthouse.example.com/v1/importers/discover \
  -d '{
    "type": "notion",
    "config": {},
    "secrets": {"integration_token": "secret_..."}
  }'
```

Response:

```json
{
  "items": [
    {
      "id": "abc-…",
      "name": "Engineering wiki",
      "kind": "database",
      "hint": "https://notion.so/abcdef",
      "config_patch": {"database_ids": "abc-…"}
    }
  ]
}
```

`config_patch` is opaque to the client — pass it through into a
subsequent `POST /v1/importers/` as `config: {...auth_fields, ...config_patch}`.

Types supporting discovery today: **notion, confluence, jira, slack, github_repo**.

### Webhooks

See [`webhooks.md`](webhooks.md) for events, signing, retry semantics.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/webhooks/` | List subscriptions. |
| `POST` | `/v1/webhooks/` | Create. Response carries the generated `secret` ONCE — store it. |
| `GET` | `/v1/webhooks/{id}` | One row (secret is redacted). |
| `PATCH` | `/v1/webhooks/{id}` | Update url/events/enabled; `rotate_secret: true` returns a new secret once. |
| `DELETE` | `/v1/webhooks/{id}` | |
| `GET` | `/v1/webhooks/{id}/deliveries?limit=50` | Recent delivery attempts. |
| `POST` | `/v1/webhooks/{id}/deliveries/{delivery_id}/redeliver` | Reset to pending; worker picks it up. |
| `POST` | `/v1/webhooks/{id}/test` | Fire a synthetic `ping` to verify URL + signing. |

### Knowledge proposals (separate flow)

Pre-existing pipeline for librarian-reviewed knowledge submissions.
Not used by Ship integration but listed for completeness.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/propose` | Submit a knowledge proposal. Key: `LIGHTHOUSE_PROPOSE_KEY`. |
| `GET` | `/v1/proposals/{id}` | Status. |

## Versioning policy

`/v1` is semver-stable. Breaking changes ship under `/v2` with a
deprecation window on `/v1`. Additive changes (new endpoints, new
fields) are non-breaking and arrive any time. The CI
`sdk-freshness` workflow checks `sdk/openapi.json` against the live
spec to catch unintended drift.
