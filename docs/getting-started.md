# Getting started with Lighthouse Engine

Point a client at a running engine and make your first call. If you
need to *bring* an engine up first, the operator-side walkthrough lives
on [harborgang.com/whafr](https://harborgang.com/whafr) — Compose, Helm,
Postgres providers, backups, upgrades. This page picks up after a
healthy `/v1/health`.

## Deployment model

Lighthouse runs as a single process talking to Postgres (with
`pgvector`). One engine per workspace — there's no built-in
multi-tenancy. If you need isolation between teams, run one
container per team.

Components:

- **`lighthouse` FastAPI** — the API. Mounts at `:8000`.
- **`postgres` + `pgvector`** — chunk store, BM25, embeddings.
- **(optional) docling-serve** — for layout-aware PDF parsing.
- **(optional) cross-encoder** — reranker; falls back to BM25+vector
  blend if absent.

The container exposes:

- `GET /health` — liveness
- `GET /docs` — Swagger UI (interactive API explorer)
- `GET /openapi.json` — machine-readable schema
- `POST /mcp/` — MCP transport, mount in any MCP client

## First call

Assuming an engine running at `https://lighthouse.example.com` and the
admin bearer you set at deploy time:

```bash
export TOK=<your admin token>

# Public retrieval — no auth needed
curl https://lighthouse.example.com/v1/search?q=OAuth+2.0+PKCE

# Admin endpoints — bearer required
curl -H "Authorization: Bearer $TOK" \
  https://lighthouse.example.com/v1/corpus/stats
```

If `/v1/corpus/stats` returns `{"total_chunks": 0, …}`, the engine
came up clean but the corpus is empty. Time to add your first
importer.

## First importer

Index FastAPI's documentation site:

```bash
curl -X POST -H "Authorization: Bearer $TOK" \
  -H "Content-Type: application/json" \
  https://lighthouse.example.com/v1/importers/ \
  -d '{
    "type": "sitemap",
    "name": "fastapi-docs",
    "recipe": "fastapi-docs",
    "config": {"root": "https://fastapi.tiangolo.com", "max_pages": 0},
    "secrets": {}
  }'
```

Response is the saved importer row, including its UUID. Fire a run:

```bash
curl -X POST -H "Authorization: Bearer $TOK" \
  https://lighthouse.example.com/v1/importers/<id>/run
```

Poll status:

```bash
curl -H "Authorization: Bearer $TOK" \
  https://lighthouse.example.com/v1/importers/<id>/runs | jq
```

Within a few minutes you'll see `status: success` with a non-zero
`chunks_added`.

## Where to go next

- [`api.md`](api.md) — full endpoint catalog.
- [`webhooks.md`](webhooks.md) — subscribe to importer events.
- [`sdk-ts.md`](sdk-ts.md) — TypeScript client.
- [`sdk-python.md`](sdk-python.md) — Python client.
- [`role-recipes.md`](role-recipes.md) — what a recipe is, how to author one.
- [`flat-rag-migration.md`](flat-rag-migration.md) — the retrieval engine internals.
- [harborgang.com/whafr](https://harborgang.com/whafr) — operator
  guide (Compose, Helm, env vars, secrets, upgrades).
