# Lighthouse local stack (Docker Compose)

Self-contained engine — Postgres + pgvector, API, and an optional
Astro admin UI. Five minutes from clone to first search.

## Bring up

```bash
cd compose/
cp .env.example .env
# Fill LIGHTHOUSE_SECRETS_KEY, LIGHTHOUSE_ADMIN_TOKEN, OPENAI_API_KEY
docker compose up -d
```

`docker compose ps` should show `postgres` and `api` healthy. Hit:

```bash
curl http://localhost:8000/health
open http://localhost:8000/docs            # Swagger UI
```

## With the admin UI

```bash
docker compose --profile web up -d         # adds Astro on :3000
open http://localhost:3000/admin/importers
```

## What gets created

- Postgres data in named volume `pgdata` (survives `compose down`;
  blow away with `docker compose down -v`).
- Schema bootstrapped automatically: chunks table (via the API's
  startup migration), and `web/sql/*.sql` (users/billing, importers,
  webhooks) — mounted into Postgres's init dir on first boot.

## Add your first importer

```bash
export TOK=$(grep ^LIGHTHOUSE_ADMIN_TOKEN .env | cut -d= -f2)

curl -X POST -H "Authorization: Bearer $TOK" \
  -H "Content-Type: application/json" \
  http://localhost:8000/v1/importers/ \
  -d '{
    "type": "sitemap",
    "name": "fastapi-docs",
    "recipe": "fastapi-docs",
    "config": {"root": "https://fastapi.tiangolo.com", "max_pages": 0},
    "secrets": {}
  }' | jq

# Trigger a run:
curl -X POST -H "Authorization: Bearer $TOK" \
  http://localhost:8000/v1/importers/<id>/run
```

## Tear down

```bash
docker compose down            # keeps the volume
docker compose down -v         # wipes Postgres data too
```

## Where to next

- Full deployment guide: [`../docs/deployment.md`](../docs/deployment.md)
- API reference: [`../docs/api.md`](../docs/api.md)
- Webhooks: [`../docs/webhooks.md`](../docs/webhooks.md)
- SDKs: [`../docs/sdk-ts.md`](../docs/sdk-ts.md) · [`../docs/sdk-python.md`](../docs/sdk-python.md)
