# User scenarios

The test plan for the engine is organized around six personas-×-starting-point
journeys. Two axes:

- **Who**: solo dev → team → organization (multi-team).
- **Docs**: starting from scratch vs. an existing corpus/instance.

Each scenario lists the journey, the invariants the engine must hold, and
where it is covered by tests. Unit-level journey tests live in
`tests/test_scenarios.py` (fakes at the asyncpg seam); real-Postgres
end-to-end versions in `tests/integration/test_scenarios_pg.py` (gated on
`LIGHTHOUSE_TEST_PG_URL`, see that file's header for the docker one-liner).

---

## S1 — Solo dev, from scratch (zero keys)

"I have a folder of notes and want Claude Code to search them. I don't
want to sign up for anything."

Journey: `docker compose up` with no API keys at all →
`LIGHTHOUSE_INSECURE_ADMIN=true` locally → add a markdown/url-list source
in /ui → run it → search from Claude Code via `/mcp/`.

Invariants:
- No OpenAI / Anthropic / admin token required for the whole loop.
- Ingest stores chunks with NULL embeddings; search runs keyword-only
  (BM25) and still finds the docs; nonsense queries return empty.
- Startup logs warn (once) about keyword-only mode and missing tokens —
  but nothing crashes.

Covered: `test_scenarios.py::TestS1*`, integration S1, quickstart doc.

## S2 — Solo dev, existing docs site

"My project already has docs at docs.example.com. I want them as an MCP
endpoint and I have an OpenAI key."

Journey: quickstart + OPENAI_API_KEY → sitemap importer → hybrid search →
dashboard fills with questions → an unanswerable query shows up in
Coverage gaps → triage it.

Invariants:
- Importer run lifecycle (queued → running → idle, run history rows).
- Searches land in query_log; gap (zero-hit) queries appear in
  /v1/analytics/gaps and can be triaged open → planned → resolved.
- A resolved gap disappears from the default gaps view.

Covered: `test_scenarios.py::TestS2*`; classifier specifics in
`test_analytics.py`.

## S3 — Team, from scratch

"Five devs + CI share one instance. Nobody outside the team may read it."

Journey: operator sets `LIGHTHOUSE_ADMIN_TOKEN` +
`LIGHTHOUSE_RETRIEVAL_AUTH_REQUIRED=true` → creates an API key per dev
and one for CI in /ui → distributes secrets → a teammate leaves, their
key is revoked.

Invariants:
- Admin surface 401 without the operator token.
- Keyless retrieval 401; header spoofing selects nothing.
- Each `lh_` key authenticates and pins its workspace; revoked key stops
  working immediately; remaining keys unaffected.
- Rate limit (when configured) throttles a runaway client with 429.

Covered: `test_scenarios.py::TestS3*`, integration S3 + HTTP smoke.

## S4 — Team, existing knowledge base (Notion/Confluence/Jira)

"Our knowledge is in Notion. Index it without leaking the integration
token."

Journey: operator sets `LIGHTHOUSE_SECRETS_KEY` → creates a Notion
importer through the wizard (secret token) → discovery picks the spaces →
runs ingest → Source analytics shows which sources actually answer
questions.

Invariants:
- Without the master key, creating a secret-bearing importer fails with
  an actionable error (and startup preflight warned about it).
- With it, secrets are stored encrypted, never echoed by any read
  endpoint (`has_secrets: true`, config visible, token absent).
- Webhook on `importer.run.finished` notifies the team's endpoint —
  HMAC-signed, scoped to the team's workspace.

Covered: `test_scenarios.py::TestS4*`; importer store/crypto unit tests.

## S5 — Organization, multi-team workspaces

"Platform team runs one engine for 12 teams. Team A must never see
team B's corpus, keys, webhooks, or analytics."

Journey: one instance, workspace per team, retrieval auth required;
the operator administers all workspaces via `X-Workspace`; each team
gets keys bound to its workspace only.

Invariants (the isolation matrix — every row enforced):

| Surface          | Isolation mechanism                          |
|------------------|----------------------------------------------|
| chunks / search  | workspace_id filter in every FlatGraph read   |
| importers + runs | workspace-scoped CRUD + ownership checks      |
| webhooks         | workspace column on subs + deliveries; emit fans out within tenant only |
| API keys         | key pins workspace; mismatch header → 403     |
| query_log / analytics | every aggregate WHERE workspace_id = …   |
| gap triage       | coverage_gap_status keyed by (workspace, query) |

Covered: `test_scenarios.py::TestS5*`, integration S5 (real SQL),
`tests/integration/test_workspace_isolation.py` (chunks),
`test_importer_workspace.py` (importers).

## S6 — Organization, brownfield upgrade

"We've run the public-corpus engine since 0.0.1. Upgrade without
breaking the public corpus, then turn auth on."

Journey: pull new version → boot (migrations 0006–0009 auto-apply on a
populated DB) → everything keeps serving keyless → operator flips
`LIGHTHOUSE_RETRIEVAL_AUTH_REQUIRED=true` when ready.

Invariants:
- Migrations are idempotent and safe on a DB with existing chunks,
  importers, webhooks (old rows grandfathered into `public`).
- Until the flag flips, keyless behaviour is byte-compatible with 0.0.1
  (X-Workspace header honored, public default).
- After the flip, the same corpus requires keys — a documented,
  deliberate switch, not a surprise.
- query_log gains api_key_id attribution → per-key usage is computable
  (billing prerequisite).

Covered: `test_scenarios.py::TestS6*`, integration S6 + migrations test.

---

## Known not-covered (candidate next work)

Surfaced while writing the scenarios — things the journeys want that the
product doesn't do yet:

- **Usage rollups**: S3/S5 operators want `GET /v1/usage` (queries per
  key/workspace per month). The data exists in query_log; the endpoint
  doesn't.
- **Workspace registry**: there is no way to LIST workspaces — the
  operator must remember tenant ids. A `workspaces` table (or
  `SELECT DISTINCT`) endpoint would fix discovery in /ui too.
- **Key scopes**: `api_keys.scopes` is stored but unenforced (everything
  is effectively `read`). Admin-scoped keys would let teams self-serve
  importers without the instance-wide operator token (the real RBAC
  seam).
- **Importer-run durability** (S2/S4): a pod restart orphans an
  in-flight run (sweep marks it cancelled; nothing retries it).
- **query_log retention** (S5/S6): unbounded growth; needs TTL or
  rollup+prune.
- **MCP transport gating**: tools enforce auth, but the /mcp session
  endpoint itself accepts unauthenticated session creation; an ingress
  rule is still recommended for public deployments.

Found while running the real-Postgres scenario suite:

- **Mixed-mode tenancy is a footgun** (S5/S6): with retrieval auth OFF
  (the default), keyless callers can read ANY workspace by guessing its
  id — even in a deployment where other teams use keys. Auth is
  all-or-nothing per instance; a per-workspace "require keys" flag (or
  simply defaulting the flag on in multi-workspace setups) would close
  it. Documented loudly in SECURITY.md until then.
- **Dead webhook deliveries are inert**: after retries exhaust there is
  no replay path (`redeliver` exists per-delivery, but nothing lists or
  bulk-requeues `dead` rows in the UI).
- **Orphan gap-triage rows**: `PATCH /v1/analytics/gaps/status` upserts
  state for arbitrary strings (even never-asked queries) and nothing
  GCs `coverage_gap_status`.
- **QueryLogger / FlatGraph pool lifecycle**: both create loop-bound
  asyncpg pools outside the DI seam (module global / lru_cache); fine
  in production (one loop), awkward for embedders and tests — needs an
  explicit close hook.
- **`openai` is an import-time dependency even in keyword-only mode**
  (`_rerank` imports before its no-key early-out). Move the import
  below the check to make the BM25-only install truly minimal.
- `tests/integration/test_migrations.py` had hardcoded the 0001–0005
  list and silently broke when 0006–0009 landed — fixed to derive the
  expectation from the migrations directory; a reminder that the
  PG-gated suite isn't in CI yet.
