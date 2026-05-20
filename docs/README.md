# Lighthouse Engine documentation

Stack-deep technical docs for integrators. Marketing pages live at
[lighthouse.harborgang.com](https://lighthouse.harborgang.com).
Operator-side guides (Compose, Helm, Postgres providers, backups,
upgrades) live at [harborgang.com/whafr](https://harborgang.com/whafr).

## Start here

| Doc | What it covers |
|---|---|
| [`getting-started.md`](getting-started.md) | First call, first importer (assumes a running engine). |
| [`api.md`](api.md) | REST endpoint catalog, auth posture, error shape, versioning. |
| [`webhooks.md`](webhooks.md) | Event catalog, HMAC signing, retry semantics, idempotency. |
| [`sdk-ts.md`](sdk-ts.md) | TypeScript client (`@lighthouse/client`) — full reference. |
| [`sdk-python.md`](sdk-python.md) | Python client (`lighthouse-client`) — full reference. |

## Internals

| Doc | What it covers |
|---|---|
| [`role-recipes.md`](role-recipes.md) | Recipe authoring — sources, schedule, role tagging. |
| [`flat-rag-migration.md`](flat-rag-migration.md) | Why pgvector + boosted tsvector + cross-encoder rerank, vs. the old Graphiti path. |
| [`source-discovery-channels.md`](source-discovery-channels.md) | How we find new sources worth indexing. |
| [`coverage-differentiators.md`](coverage-differentiators.md) | What's in the corpus the alternatives miss. |
| [`coverage-vs-context7.md`](coverage-vs-context7.md) | Head-to-head with context7 on a benchmark set. |

## Live + interactive

- **Swagger UI:** `https://your-engine/docs`
- **ReDoc:** `https://your-engine/redoc`
- **OpenAPI schema:** `https://your-engine/openapi.json`

These are auto-generated from the running FastAPI app — always
match the deployed version exactly.
