# Quickstart — zero to MCP endpoint in 10 minutes

> Lighthouse Engine is **pre-alpha (0.0.x)**. The `/v1` HTTP surface is
> intended to stay stable, but everything else — schema, admin UI,
> importer configs — may change between releases. Don't bet production
> traffic on it yet.

Prerequisites: Docker with Compose v2.

## 1. Clone and configure

```bash
git clone https://github.com/ElMundiUA/whafr.git
cd whafr
cp .env.example .env
```

With docker-compose you don't need to touch `.env` at all —
`LIGHTHOUSE_PG_URL` is already set on the engine service. Two values
worth setting:

- `OPENAI_API_KEY` — optional. Without it the engine runs in
  keyword-only search mode (BM25, no vector retrieval, no rerank).
  Set it for full hybrid search.
- `LIGHTHOUSE_ADMIN_TOKEN` — set one if anything other than your own
  machine can reach port 8000.

## 2. Start

```bash
docker compose up --build
```

Two containers come up: `db` (Postgres + pgvector) and `engine`.
Migrations run automatically on startup. The engine is ready when
`curl http://localhost:8000/health` returns OK.

## 3. Add a source

Open the admin UI: <http://localhost:8000/ui/>

Add an importer — easiest first source is a **sitemap** pointed at
docs you care about:

- Type: `sitemap`
- Name: `my-docs`
- Config root: `https://fastapi.tiangolo.com` (or your own docs site)

Click **Run**. Ingestion takes a few minutes for a typical docs site;
the run status updates in the UI.

(Prefer curl? The same flow over the API is in
[`docs/getting-started.md`](docs/getting-started.md).)

## 4. Verify search works

```bash
curl "http://localhost:8000/v1/search?q=dependency+injection&top_k=3"
```

You should get ranked hits with summaries. Empty results usually mean
the importer run hasn't finished — check the run status in the UI.

## 5. Connect Claude Code

Drop a `.mcp.json` in any project:

```json
{
  "mcpServers": {
    "my-docs": {
      "type": "http",
      "url": "http://localhost:8000/mcp/"
    }
  }
}
```

Or register it from the terminal:

```bash
claude mcp add --transport http my-docs http://localhost:8000/mcp/
```

Restart Claude Code and it can `search` / `fetch_source` against your
corpus. Any MCP client that speaks streamable-http works the same way
— point it at `/mcp/`.

## Next steps

- [`docs/api.md`](docs/api.md) — full REST catalog.
- [`docs/webhooks.md`](docs/webhooks.md) — importer event webhooks.
- [`SECURITY.md`](SECURITY.md) — read before exposing the engine
  beyond localhost.
