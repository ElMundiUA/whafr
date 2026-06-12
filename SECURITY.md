# Security Policy

## Supported versions

| Version | Supported |
|---|---|
| 0.x (latest release) | Yes |
| anything older | No |

Pre-1.0: only the most recent 0.x release receives security fixes.

## Reporting a vulnerability

<!-- TODO(team): confirm this address exists and is monitored. -->
Email **security@harborgang.com**. Do not open a public GitHub issue
for security reports.

Include: affected version/commit, reproduction steps, and impact
assessment if you have one.

What to expect:

- Acknowledgement within 7 days.
- We ask for a **90-day** disclosure window from acknowledgement
  before public disclosure; we'll coordinate an earlier date if a fix
  ships sooner.

## Deployment security model

Things to know before exposing an engine beyond localhost:

- **Retrieval is public by default.** `/v1/search`, `/v1/fetch`, and
  `/mcp/` require no auth unless you set
  `LIGHTHOUSE_RETRIEVAL_AUTH_REQUIRED=true` (per-workspace API keys).
  In production, either enable that or network-isolate the engine
  (private network, VPN, reverse-proxy auth).
- **The admin surface requires `LIGHTHOUSE_ADMIN_TOKEN`.** The admin
  UI (`/ui`) and admin API (importers, webhooks, analytics) are gated
  by this bearer token. `LIGHTHOUSE_INSECURE_ADMIN=true` disables the
  check — local development only, never in production.
- **Importer credentials are encrypted at rest** with the Fernet key
  in `LIGHTHOUSE_SECRETS_KEY`. Treat that key like a database
  password; rotating it invalidates stored importer secrets.
- **Rate limiting** for search is available via
  `LIGHTHOUSE_SEARCH_RATE_LIMIT_PER_MINUTE` (0 = off).
- **`/metrics` (Prometheus) is unauthenticated** by scraping
  convention. It exposes route/latency/counter telemetry (no query
  text, no corpus content) — still, firewall it at the ingress on
  public deployments.

## Data flows to third parties

The engine calls external APIs only when you configure keys for them:

| Party | What is sent | Why | How to disable |
|---|---|---|---|
| OpenAI | Chunk text and query text | Embeddings, search-time rerank, relevance gate | Leave `OPENAI_API_KEY` empty — engine falls back to keyword-only (BM25) search |
| Anthropic | Proposal content; logged queries + hit summaries | Librarian agent; LLM gap classifier | Leave `ANTHROPIC_API_KEY` empty; keep `LIGHTHOUSE_GAP_CLASSIFIER_ENABLED=false` |

With neither key set, no document or query content leaves your
infrastructure.

## Telemetry

The engine sends **no telemetry** to the project authors. There is no
phone-home, usage reporting, or update check of any kind.
