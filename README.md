# Lighthouse Engine

Open-source retrieval layer for grounded coding agents. Hybrid BM25 +
pgvector + cross-encoder rerank, served over HTTP + MCP. Pluggable
importer layer covering 30 source types out of the box: docs (sitemap,
github), team knowledge (Notion, Confluence, Slack, Linear, Jira),
storage (S3, GCS, Azure Blob, Google Drive, Box), structured data
(Postgres, MongoDB, Airtable), forums (Reddit, Stack Overflow), and
more.

Same code powers two products:

- **[lighthouse.harborgang.com](https://lighthouse.harborgang.com)** —
  hosted SaaS over a curated SDLC corpus (RFCs, OWASP, NIST,
  framework docs).
- **Engine** — self-hosted, bring-your-own-corpus. This repo.

Apache-2.0.

## What's in here

```
src/lighthouse/
  api/            FastAPI app (/v1 + /admin + /mcp)
  core/           FlatGraph: pgvector index + BM25 + rerank
  connectors/     Source connectors: sitemap, github_tree, rss, web, …
  importers/      Admin-managed importer layer + 30 adapters
  webhooks/       Outbound webhook dispatcher (HMAC, retry)
  mcp/            MCP server (streamable-http transport)
infra/            Docker + k8s manifests
web/              Astro public + admin frontend
sdk/
  ts/             @lighthouse/client     — TypeScript SDK
  python/         lighthouse-client      — Python SDK
docs/             Operator + integrator documentation
```

## Quickstart

```bash
docker run -d --name lighthouse \
  -p 8000:8000 \
  -e LIGHTHOUSE_PG_URL=postgresql://user:pw@host:5432/lighthouse \
  -e LIGHTHOUSE_SECRETS_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())") \
  -e LIGHTHOUSE_ADMIN_TOKEN=$(openssl rand -base64 32) \
  -e OPENAI_API_KEY=sk-… \
  ghcr.io/elmundiua/lighthouse:latest

curl http://localhost:8000/health
open http://localhost:8000/docs           # Swagger UI
```

Full walkthrough: [`docs/getting-started.md`](docs/getting-started.md).

## Programmatic use

```ts
import { createClient } from "@lighthouse/client";

const lh = createClient({ baseUrl: "https://your-engine", token });

const { hits } = await lh.search("OAuth 2.0 PKCE S256", { top_k: 5 });
const imp = await lh.importers.create({
  type: "sitemap",
  name: "fastapi-docs",
  recipe: "fastapi-docs",
  config: { root: "https://fastapi.tiangolo.com", max_pages: 0 },
});
await lh.importers.run(imp.id);
```

```python
from lighthouse_client import AsyncLighthouse

async with AsyncLighthouse("https://your-engine", token=tok) as lh:
    stats = await lh.corpus_stats()
    imp = await lh.create_importer(
        type="sitemap", name="fastapi-docs", recipe="fastapi-docs",
        config={"root": "https://fastapi.tiangolo.com", "max_pages": 0},
    )
    await lh.run_importer(imp.id)
```

## MCP

```bash
# Local stdio (Claude Desktop, Cursor)
lighthouse mcp

# HTTP transport — already mounted at /mcp/ when the API is running
# Point your MCP client at: https://your-engine/mcp/
```

## Documentation

| Doc | What it covers |
|---|---|
| [`docs/getting-started.md`](docs/getting-started.md) | Boot an engine, first call, first importer. |
| [`docs/api.md`](docs/api.md) | REST endpoint catalog. |
| [`docs/webhooks.md`](docs/webhooks.md) | Event payloads + HMAC signing + retry. |
| [`docs/sdk-ts.md`](docs/sdk-ts.md) | TypeScript SDK reference. |
| [`docs/sdk-python.md`](docs/sdk-python.md) | Python SDK reference. |
| [`docs/role-recipes.md`](docs/role-recipes.md) | Recipe authoring. |
| [`docs/flat-rag-migration.md`](docs/flat-rag-migration.md) | Retrieval engine internals. |

Interactive Swagger UI ships at `/docs` on every engine; full
OpenAPI schema at `/openapi.json` (also committed at
[`sdk/openapi.json`](sdk/openapi.json)).

## License

Apache-2.0. Curated corpus shipped on the hosted SaaS is separate
and not included in this repo.
