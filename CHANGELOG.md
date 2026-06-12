# Changelog

All notable changes to Lighthouse Engine are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Pre-1.0: minor releases may contain breaking changes; the `/v1` HTTP
surface is intended to stay stable.

## [Unreleased]

### Added

- Durable importer-run queue (migration 0011): `POST /{id}/run` now
  persists a `queued` run row (and returns the real run id); a
  run-queue worker claims rows with `FOR UPDATE SKIP LOCKED`
  (replica-safe) and executes them. The boot sweep re-queues a run
  orphaned by a pod restart once before cancelling — runs are no
  longer silently lost.
- `/metrics` Prometheus endpoint: HTTP requests/latency by route
  template, searches by workspace/gap, importer runs by status,
  webhook delivery outcomes.
- Configurable asyncpg pool bounds (`LIGHTHOUSE_PG_POOL_MIN/MAX`;
  the old hardcoded max of 5 queued up under load).

- `GET /v1/usage` — billing-shaped rollups over `query_log`: searches
  per API key (keyless legacy traffic = null key) and per day, scoped
  to the workspace. Read-side only; quota enforcement is deliberately
  deferred until plans exist.
- CI workflow (`.github/workflows/ci.yml`): ruff + the full pytest
  suite against a pgvector service container, so the PG-gated
  integration scenarios finally run on every push/PR.

- Workspace registry with per-workspace auth policy (migration 0010):
  `GET/PUT /v1/workspaces` + a Workspaces page in `/ui`. A workspace
  with `require_auth = true` rejects keyless retrieval even while the
  instance-wide default stays open — closes the mixed-mode gap where
  any tenant was readable by guessing its id.
- `POST /v1/webhooks/{id}/deliveries/requeue-dead` (+ UI button):
  bulk-requeue deliveries whose retries were exhausted.
- `POST /v1/analytics/gaps/prune?days=N`: garbage-collect gap-triage
  state for query clusters nobody asks anymore.
- Persona-based scenario suite: `docs/scenarios.md` plus journey tests
  (`tests/test_scenarios.py`) and a real-Postgres end-to-end suite
  (`tests/integration/test_scenarios_pg.py`, gated on
  `LIGHTHOUSE_TEST_PG_URL`).
- Built-in admin UI served at `/ui` (importers, runs, webhooks).
- Query analytics and coverage-gap reporting: `query_log` table and
  `/v1/analytics` endpoints.
- LLM gap classifier (`LIGHTHOUSE_GAP_CLASSIFIER_ENABLED`) — rates
  search-hit usefulness with Claude Haiku, off the request path, to
  flag low-quality coverage even when hits come back.
- Per-workspace API keys for retrieval
  (`LIGHTHOUSE_RETRIEVAL_AUTH_REQUIRED`).
- Auth hardening: `LIGHTHOUSE_ADMIN_TOKEN` moved into Settings,
  explicit `LIGHTHOUSE_INSECURE_ADMIN` opt-out, search rate limiting
  (`LIGHTHOUSE_SEARCH_RATE_LIMIT_PER_MINUTE`).
- Keyword-only (BM25) degraded search mode when no `OPENAI_API_KEY`
  is configured.
- Docker packaging: `Dockerfile`, `docker-compose.yml`,
  `QUICKSTART.md`, `SECURITY.md`.
- Per-workspace importer tenancy and per-workspace S3 importer
  provisioning.
- Webhook workspace isolation (migration 0005) and the
  `lighthouse run-importers` cron command.
- `discover()` support for github_releases, gitlab, bitbucket,
  linear, asana, and trello importers.

### Changed

- Removed the Graphiti/Neo4j backend — the engine is flat-RAG
  (Postgres + pgvector) only.
- `.env.example` rewritten to match the current Settings model
  (Neo4j variables removed).

### Fixed

- Migrations now run automatically on API startup.
- `QueryLogger` accepts an injectable pool factory; the API lifespan
  closes the retrieval engine's asyncpg pool on shutdown.

## [0.0.1] - 2026-06

### Added

- Initial pre-alpha extraction of the engine:
  - Flat-RAG retrieval: hybrid BM25 + pgvector + cross-encoder rerank
    over Postgres.
  - HTTP API (`/v1`) with Swagger UI at `/docs`.
  - MCP server (stdio + streamable-http, mounted at `/mcp/`).
  - Importer layer with 30 source adapters (sitemap, GitHub, Notion,
    Confluence, Slack, S3, databases, …) behind optional extras.
  - Outbound webhooks with HMAC signing and retry.
  - Proposal pipeline with git-backed store and Librarian agent.
  - TypeScript and Python SDKs.
