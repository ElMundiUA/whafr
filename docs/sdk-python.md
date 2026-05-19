# `lighthouse-client` — Python SDK

Async-first httpx wrapper over the Lighthouse engine API. Sync mirror
included for ad-hoc scripts. Pydantic-validated models.

## Install

```bash
pip install lighthouse-client
```

Requires Python 3.10+. Dependencies: `httpx`, `pydantic`.

## Async (canonical)

```python
from lighthouse_client import AsyncLighthouse

async with AsyncLighthouse(
    "https://lighthouse.example.com",
    token=os.environ["LIGHTHOUSE_ADMIN_TOKEN"],   # omit for public engines
) as lh:
    stats = await lh.corpus_stats()
    print(f"{stats.total_chunks} chunks, last ingest {stats.last_ingest_at}")

    hits = await lh.search("Postgres EXPLAIN cost", top_k=5)
    for h in hits.hits:
        print(h.summary)
```

The client opens one `httpx.AsyncClient` and reuses it across calls —
keep it alive (long-lived service, FastAPI lifespan, etc.) instead of
constructing per-request.

## Sync (one-shot scripts)

```python
from lighthouse_client import Lighthouse

with Lighthouse("https://lighthouse.example.com", token=tok) as lh:
    print(lh.corpus_stats())
```

The sync surface covers the common endpoints (`search`,
`corpus_stats`, `importers`, `run_importer`) but is intentionally
slim — for full coverage stick with `AsyncLighthouse`.

## Retrieval

```python
hits = await lh.search("OAuth 2.0 PKCE S256", top_k=5, sort="relevance")
for h in hits.hits:
    print(h.summary, h.source)

entity = await lh.fetch_entity("0123-…")
source = await lh.fetch_source("ep-…")
```

## Corpus

```python
stats = await lh.corpus_stats()
sources = await lh.corpus_sources(limit=50, order="chunks")
for s in sources:
    print(s.source, s.chunk_count, s.recipes)
```

## Importers

```python
# Catalog
for t in await lh.importer_types():
    print(t.type, t.display_name, "discoverable" if t.supports_discovery else "")

# Create + run
imp = await lh.create_importer(
    type="sitemap",
    name="fastapi-docs",
    recipe="fastapi-docs",
    config={"root": "https://fastapi.tiangolo.com", "max_pages": 0},
)
await lh.run_importer(imp.id)

# Recent runs
runs = await lh.importer_runs(imp.id)
print(runs[0].status, runs[0].chunks_added)
```

### Discovery wizard

```python
items = await lh.discover(
    type="notion",
    config={},
    secrets={"integration_token": "secret_…"},
)
for it in items[:5]:
    print(it.kind, it.name, it.hint)

# Create one importer per picked item
for it in items[:3]:
    slug = it.name.lower().replace(" ", "-")
    await lh.create_importer(
        type="notion",
        name=f"notion-{slug}",
        recipe=f"notion-{it.id[:8]}",
        config=it.config_patch,
        secrets={"integration_token": "secret_…"},
    )
```

## Webhooks

```python
wh = await lh.create_webhook(
    url="https://your-app.example.com/hooks/lighthouse",
    events=["importer.run.finished"],
)
# PERSIST wh.secret — only echoed once.

await lh.test_webhook(wh.id)          # ping

deliveries = await lh.webhook_deliveries(wh.id, limit=20)
await lh.redeliver_webhook(wh.id, deliveries[0].id)
```

### Receiver

```python
# FastAPI handler example
from fastapi import APIRouter, Header, Request, HTTPException
from lighthouse_client import verify_webhook_signature

router = APIRouter()
SECRET = os.environ["LIGHTHOUSE_WEBHOOK_SECRET"]

@router.post("/hooks/lighthouse")
async def receive(
    request: Request,
    sig: str = Header(alias="X-Lighthouse-Signature"),
):
    body = await request.body()                    # RAW bytes
    if not verify_webhook_signature(SECRET, body, sig):
        raise HTTPException(status_code=401, detail="bad signature")
    event = json.loads(body)
    if event["event"] == "importer.run.finished":
        ...                                         # react
    return {"ok": True}
```

Always verify against the raw request body — re-parsing/dumping the
JSON changes whitespace and breaks the signature.

## Errors

```python
from lighthouse_client import LighthouseError

try:
    await lh.run_importer(some_id)
except LighthouseError as e:
    if e.status == 409:
        ...                # already running
    elif e.status == 401:
        raise SystemExit("Token rejected — check LIGHTHOUSE_ADMIN_TOKEN")
    else:
        raise
```

`LighthouseError.body` is the raw response text — useful when
Lighthouse forwards a meaningful detail (e.g. a Pydantic validation
error or an upstream Notion 4xx).

## Pydantic models

All response shapes are pydantic v2 models. Re-export:

```python
from lighthouse_client import (
    CorpusStats, CorpusSource,
    Importer, ImporterRun, ImporterType, DiscoveredItem,
    SearchHit,
    Webhook, WebhookCreated,
)
```

Pass them around freely; their `.model_dump()` round-trips back into
a dict matching the API exactly.

## Versioning

Client tracks the engine's `/v1` surface. Bumps:

- **Patch** — bug fixes, no field changes.
- **Minor** — added fields, added methods (backwards-compatible).
- **Major** — removed/renamed methods (rare; aligns with engine `/v2`).
