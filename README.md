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
  api/            FastAPI app: /search, /fetch, /propose
  core/           Config + Graphiti+FalkorDB wrapper
  connectors/     Source connectors (markdown today, LlamaIndex-hub backed)
  librarian/      Curator agent (Anthropic SDK + prompt caching)
infra/            docker-compose for local dev (FalkorDB + lighthouse-api)
tests/            Smoke tests
```

## Quickstart

```bash
cp .env.example .env
# Fill in ANTHROPIC_API_KEY and LIGHTHOUSE_PROPOSAL_API_KEY

docker compose -f infra/docker-compose.yml up -d   # FalkorDB on localhost:6379
pip install -e ".[dev]"
uvicorn lighthouse.api.main:app --reload
```

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
