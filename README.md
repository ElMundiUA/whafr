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

> **Looking to run this?** Operator-side docs (Compose, Helm, K8s,
> backups, upgrades) live on [harborgang.com/whafr](https://harborgang.com/whafr).
> This repo carries the engine itself — code, SDKs, recipe authoring,
> internals. Pin a `sha-*` tag from there.

## Quickstart

```bash
git clone https://github.com/ElMundiUA/whafr.git && cd whafr
cp .env.example .env   # OPENAI_API_KEY optional — keyword-only search without it
docker compose up --build
```

Admin UI at <http://localhost:8000/ui/>, MCP at `/mcp/`. Full
walkthrough — first importer, verifying search, connecting Claude
Code — in [`QUICKSTART.md`](QUICKSTART.md).

## Versioning

The `/v1` HTTP surface is intended to stay stable. The engine itself
is 0.x pre-1.0 — anything else (schema, admin UI, importer configs)
may break between minor releases. See [`CHANGELOG.md`](CHANGELOG.md).

Telemetry: none — the engine phones home to no one.

## What's in here

```
src/lighthouse/
  api/            FastAPI app (/v1 + /admin + /mcp)
  core/           FlatGraph: pgvector index + BM25 + rerank
  connectors/     Source connectors: sitemap, github_tree, rss, web, …
  importers/      Admin-managed importer layer + 30 adapters
  webhooks/       Outbound webhook dispatcher (HMAC, retry)
  mcp/            MCP server (streamable-http transport)
sdk/
  ts/             @lighthouse/client     — TypeScript SDK
  python/         lighthouse-client      — Python SDK
docs/             Engine reference: API, SDKs, recipes, internals
```

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
| [`docs/getting-started.md`](docs/getting-started.md) | First call, first importer. |
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
