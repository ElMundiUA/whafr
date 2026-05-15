# Lighthouse

Knowledge base for AI agents. Retrieval over MCP, proposals over HTTP, temporal
knowledge graph underneath, no tenant ceremony.

The same engine ships in two deployment patterns:

- **Global instance** — public read-only retrieval, sources authoritative tech
  docs, librarian curates incoming proposals. One deployment everyone consumes.
- **Project instance** — private retrieval, project-specific sources,
  project-specific librarian calibration. One deployment per project.

Isolation is achieved by deploying separate instances against separate
databases. The code has no tenant model; the two deployments simply never share
a Postgres or graph store.

## What's in here

```
src/lighthouse/
  api/            FastAPI app: /search, /fetch_entity, /fetch_source, /propose
  core/           Config + Graphiti+Neo4j wrapper
  connectors/     Source connectors (markdown, web, github, sitemap)
  librarian/      Curator agent (Anthropic SDK + prompt caching)
infra/            docker-compose + k8s manifests (Neo4j 5.26 CE + lighthouse-api)
tests/            Smoke tests
```

## Self-host: graph backend

Lighthouse v0.2+ runs on **Neo4j 5.26 Community Edition** (GPLv3, free for
self-hosters). Earlier versions used FalkorDB; we moved off it because its
BSL ("Business Source License") is source-available, not OSS — a problem for
an opensource project. The Neo4j swap is a one-line config change for
existing deployments; data does not migrate automatically (re-ingest from
sources).

## Quickstart

```bash
cp .env.example .env
# Fill in ANTHROPIC_API_KEY, OPENAI_API_KEY, LIGHTHOUSE_PROPOSAL_API_KEY

docker compose -f infra/docker-compose.yml up -d   # Neo4j on bolt://localhost:7687
pip install -e ".[dev]"
uvicorn lighthouse.api.main:app --reload
```

Neo4j browser is on http://localhost:7474 — default creds `neo4j` /
`neo4j_dev_password` (change in compose + `.env` before exposing anywhere).

Then:

```bash
curl http://localhost:8000/health
curl 'http://localhost:8000/search?q=hello&top_k=5'
```

## Ingesting content

```bash
# A local directory of markdown files
lighthouse ingest markdown ./docs

# One or more web pages (BeautifulSoup-parsed; no JS rendering)
lighthouse ingest web https://example.com/post1 https://example.com/post2

# Doc files from a GitHub repo (.md / .rst / .mdx / .txt by default)
GITHUB_TOKEN=ghp_... lighthouse ingest github fastapi/fastapi --branch master

# Restrict file types explicitly
lighthouse ingest github encode/starlette --ext .md .rst
```

## MCP server (for AI clients)

```bash
# Desktop clients (Claude Desktop, Cursor) spawn this over stdio
lighthouse mcp

# Remote agents use HTTP transports
lighthouse mcp --transport http --port 8765
```

## License

Apache 2.0 — see [LICENSE](./LICENSE).
