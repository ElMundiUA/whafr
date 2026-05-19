# Getting started with Lighthouse Engine

Stand up an engine, point a client at it, run your first search.
Five minutes from clone to citation.

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

## Run with Docker

```bash
docker run -d --name lighthouse \
  -p 8000:8000 \
  -e LIGHTHOUSE_PG_URL=postgresql://user:pw@host:5432/lighthouse \
  -e LIGHTHOUSE_SECRETS_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") \
  -e LIGHTHOUSE_ADMIN_TOKEN=$(openssl rand -base64 32) \
  -e OPENAI_API_KEY=sk-… \
  ghcr.io/elmundiua/lighthouse:latest
```

The container exposes:

- `GET /health` — liveness
- `GET /docs` — Swagger UI (interactive API explorer)
- `GET /openapi.json` — machine-readable schema
- `POST /mcp/` — MCP transport, mount in any MCP client

## Required environment

| Var | Purpose | Required |
|---|---|---|
| `LIGHTHOUSE_PG_URL` | Postgres DSN (pgvector available) | yes |
| `LIGHTHOUSE_SECRETS_KEY` | Fernet master key for encrypted importer secrets | yes (any importer that stores tokens) |
| `LIGHTHOUSE_ADMIN_TOKEN` | Shared bearer token for `/v1/*` admin surface | yes (any non-public deployment) |
| `OPENAI_API_KEY` | Embeddings + chunk summarisation | yes |
| `OPENROUTER_API_KEY` | Alternative LLM provider | optional |
| `ANTHROPIC_API_KEY` | Cross-encoder rerank, librarian | optional |
| `LIGHTHOUSE_BACKEND` | `flat` (default) or `graphiti` | optional |

Generate fresh values on first deploy and store them in your secret
manager — there's no recovery if `LIGHTHOUSE_SECRETS_KEY` is lost (every
encrypted importer config becomes garbage).

## First call

Once the container is up:

```bash
export TOK=<the LIGHTHOUSE_ADMIN_TOKEN you set above>

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
