# Changelog

All notable changes to Lighthouse Engine are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Pre-1.0: minor releases may contain breaking changes; the `/v1` HTTP
surface is intended to stay stable.

## [Unreleased]

### Added

- Built-in admin UI served at `/ui` (importers, runs, webhooks).
- Query analytics and coverage-gap reporting: `query_log` table and
  `/v1/analytics` endpoints.
- LLM gap classifier (`LIGHTHOUSE_GAP_CLASSIFIER_ENABLED`) — rates
  search-hit usefulness with Claude Haiku, off the request path, to
  flag low-quality coverage even when hits come back.
- Per-workspace API keys for retrieval
  (`LIGHTHOUSE_RETRIEVAL_AUTH_REQUIRED`). *(In progress.)*
- Auth hardening: `LIGHTHOUSE_ADMIN_TOKEN` moved into Settings,
  explicit `LIGHTHOUSE_INSECURE_ADMIN` opt-out, search rate limiting
  (`LIGHTHOUSE_SEARCH_RATE_LIMIT_PER_MINUTE`). *(In progress.)*
- Keyword-only (BM25) degraded search mode when no `OPENAI_API_KEY`
  is configured. *(In progress.)*
- Docker packaging: `Dockerfile`, `docker-compose.yml`,
  `QUICKSTART.md`, `SECURITY.md`. *(This batch.)*
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
