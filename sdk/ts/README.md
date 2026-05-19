# @lighthouse/client

TypeScript client for the Lighthouse engine API. Auto-generated types
from the running server's `/openapi.json` plus a thin fetch wrapper.

## Install

```bash
pnpm add @lighthouse/client     # or npm / yarn
```

## Usage

```ts
import { createClient } from "@lighthouse/client";

const lh = createClient({
  baseUrl: "https://lighthouse.example.com",
  token: process.env.LIGHTHOUSE_ADMIN_TOKEN, // optional unless engine requires it
});

const stats = await lh.corpus.stats();
console.log(`${stats.total_chunks} chunks across ${stats.total_sources} sources`);

const hits = await lh.search("OAuth 2.0 PKCE", { top_k: 5 });
for (const hit of hits.hits) console.log(hit.summary);

// Create an importer programmatically:
const created = await lh.importers.create({
  type: "sitemap",
  name: "fastapi-docs",
  recipe: "fastapi-docs",
  config: { root: "https://fastapi.tiangolo.com", max_pages: 0 },
});
await lh.importers.run(created.id);

// Subscribe to events:
const wh = await lh.webhooks.create({
  url: "https://your-app.example.com/webhooks/lighthouse",
  events: ["importer.run.finished"],
});
// Store wh.secret — it isn't returned again. Verify deliveries with:
import { verifyWebhookSignature } from "@lighthouse/client";
const ok = await verifyWebhookSignature(
  wh.secret,
  rawBodyBytes,
  request.headers["x-lighthouse-signature"],
);
```

## Re-generating types

The TS interface mirrors the running API. After any engine change:

```bash
pnpm regen                  # uses ../openapi.json (checked in)
pnpm regen:from-running     # hits http://localhost:8000/openapi.json
```

CI regenerates on every push to `main`; the resulting diff fails the
build if `openapi.json` falls out of sync with the type file.
