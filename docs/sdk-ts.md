# `@lighthouse/client` — TypeScript SDK

Type-safe client for the Lighthouse engine, auto-generated from the
server's OpenAPI schema plus a thin fetch wrapper. Runs on Node 18+,
Bun, Deno, Cloudflare Workers, browsers.

## Install

```bash
pnpm add @lighthouse/client    # or npm / yarn / bun
```

The package itself is ~6 KB; the bulk of weight is generated type
defs that get tree-shaken at build time.

## Initialise

```ts
import { createClient } from "@lighthouse/client";

const lh = createClient({
  baseUrl: "https://lighthouse.example.com",
  token: process.env.LIGHTHOUSE_ADMIN_TOKEN, // omit for fully public engines
});
```

`token` is appended as `Authorization: Bearer …` on every request.
The client does no caching — pair it with React Query / SWR / your
preferred data layer on the consumer side.

## Retrieval

```ts
const { hits } = await lh.search("OAuth 2.0 PKCE S256", { top_k: 5 });
for (const h of hits) console.log(h.summary, h.source);

const entity = await lh.fetchEntity("0123-…");
const source = await lh.fetchSource("ep-…");      // raw paragraph
```

## Corpus

```ts
const stats = await lh.corpus.stats();
// { total_chunks, total_sources, total_recipes,
//   chunks_with_summary, chunks_with_embedding, last_ingest_at }

const top = await lh.corpus.sources({ limit: 50, order: "chunks" });
for (const s of top) console.log(s.source, s.chunk_count, s.recipes);
```

## Importers — programmatic CRUD

```ts
// Catalog
const types = await lh.importers.listTypes();

// Create + run
const imp = await lh.importers.create({
  type: "sitemap",
  name: "fastapi-docs",
  recipe: "fastapi-docs",
  config: { root: "https://fastapi.tiangolo.com", max_pages: 0 },
});
await lh.importers.run(imp.id);

// Poll
const [latest] = await lh.importers.runs(imp.id);
console.log(latest.status, latest.chunks_added);

// Discovery wizard — Notion picker example
const { items } = await lh.importers.discover({
  type: "notion",
  config: {},
  secrets: { integration_token: "secret_…" },
});

// User picks 3 items; create one importer per pick
for (const item of items.slice(0, 3)) {
  await lh.importers.create({
    type: "notion",
    name: `notion-${item.name.toLowerCase().replace(/\W+/g, "-")}`,
    recipe: `notion-${item.id.slice(0, 8)}`,
    config: { ...item.config_patch },
    secrets: { integration_token: "secret_…" },
  });
}
```

## Webhooks

```ts
const wh = await lh.webhooks.create({
  url: "https://your-app.example.com/hooks/lighthouse",
  events: ["importer.run.finished"],
});
// PERSIST wh.secret — it isn't returned again.

// Send a test ping to verify the URL + signature path:
await lh.webhooks.test(wh.id);

// Inspect recent delivery attempts:
const recent = await lh.webhooks.deliveries(wh.id, { limit: 20 });

// Re-fire a failed one:
await lh.webhooks.redeliver(wh.id, recent[0].id);
```

### Receiver

```ts
// Next.js route handler example
import { verifyWebhookSignature } from "@lighthouse/client";

export async function POST(req: Request) {
  const body = await req.arrayBuffer();             // raw bytes
  const sig = req.headers.get("x-lighthouse-signature");
  if (!sig || !(await verifyWebhookSignature(SECRET, new Uint8Array(body), sig))) {
    return new Response("bad signature", { status: 401 });
  }
  const event = JSON.parse(new TextDecoder().decode(body));
  switch (event.event) {
    case "importer.run.finished":
      // …react to the run
      break;
  }
  return new Response(null, { status: 204 });
}
```

`verifyWebhookSignature` uses Web Crypto, so it runs unchanged on
edge runtimes. Pass `body` as the raw bytes — do **not** re-stringify
the parsed JSON, that breaks verification.

## Error handling

```ts
import { LighthouseError } from "@lighthouse/client";

try {
  await lh.importers.run(id);
} catch (e) {
  if (e instanceof LighthouseError) {
    if (e.status === 409) {
      // already running — fine to ignore
    } else if (e.status === 401) {
      throw new Error("Lighthouse token rejected — check LIGHTHOUSE_ADMIN_TOKEN");
    } else {
      throw e;
    }
  }
}
```

## Types

All response and request shapes are re-exported from the package:

```ts
import type {
  Importer, ImporterCreate, ImporterUpdate, ImporterType, ImporterRun,
  DiscoveredItem, CorpusStats, CorpusSource,
  Webhook, WebhookCreated, Delivery,
} from "@lighthouse/client";
```

Want more? Import directly from `@lighthouse/client/types` —
that's the raw `components["schemas"]` map produced by
openapi-typescript.

## Regenerating after engine upgrades

After updating to a newer engine version, regenerate the types:

```bash
cd sdk/ts
pnpm regen                # uses ../openapi.json (checked in)
pnpm regen:from-running   # hits http://localhost:8000/openapi.json
```

CI fails any push where the committed `openapi.d.ts` doesn't match
the spec — keeps the SDK from silently drifting.
