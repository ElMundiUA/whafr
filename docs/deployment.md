# Deployment

Two supported deployment patterns. Pick by where you're running.

| Target | Use this |
|---|---|
| Laptop / single-VM / dev | [Docker Compose](#docker-compose) |
| Kubernetes cluster | [Helm chart](#helm) |
| Cloud serverless | API container is a 12-factor app — drop into Cloud Run, Fly, Render, etc. and BYO Postgres. |

Both paths assume **Postgres with `pgvector` enabled**. Lighthouse
doesn't ship its own Postgres in production (see [Postgres
prerequisites](#postgres-prerequisites)).

## Docker Compose

For local dev, demos, single-VM self-host. Postgres is bundled
(`pgvector/pgvector:pg17`) so it's truly one-line.

```bash
cd compose/
cp .env.example .env
# Edit .env: LIGHTHOUSE_SECRETS_KEY, LIGHTHOUSE_ADMIN_TOKEN, OPENAI_API_KEY
docker compose up -d
# + Astro admin UI:
docker compose --profile web up -d
```

What you get:

- `lighthouse-pg` — pgvector 17, data in named volume `pgdata`.
- `lighthouse-api` — engine on `:8000`. Health-gated on Postgres.
- `lighthouse-web` (only if you used `--profile web`) — Astro admin
  UI on `:3000`.

Migrations run automatically: SQL files in `web/sql/` are mounted
into `/docker-entrypoint-initdb.d/` on Postgres's first start.

### Tear down

```bash
docker compose down         # stop containers, keep data
docker compose down -v      # also wipe the postgres volume
```

Full walkthrough: [`../compose/README.md`](../compose/README.md).

## Helm

For production k8s. Bring your own Postgres.

```bash
helm install lighthouse charts/lighthouse \
  --namespace lighthouse --create-namespace \
  --set postgres.url=postgresql://user:pw@pg.svc:5432/lighthouse \
  --set env.secretsKey="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
  --set env.adminToken="$(openssl rand -base64 32)" \
  --set env.openaiKey=$OPENAI_API_KEY
```

Production-grade values:

```yaml
# values.prod.yaml
image:
  tag: "0.1.0"               # pin instead of `latest`

postgres:
  existingSecret: lighthouse-pg-url    # carries LIGHTHOUSE_PG_URL

existingEnvSecret: lighthouse-env      # carries secretsKey, adminToken, openaiKey, ...

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: lighthouse.acme.com
      paths: [{ path: /, pathType: Prefix }]
  tls:
    - secretName: lighthouse-tls
      hosts: [lighthouse.acme.com]

resources:
  requests: { cpu: 250m, memory: 1Gi }
  limits:   { cpu: 2,    memory: 4Gi }

web:
  enabled: true              # optional Astro admin UI
  ingress:
    enabled: true
    className: nginx
    hosts: [{ host: lighthouse-admin.acme.com, paths: [{ path: /, pathType: Prefix }] }]
```

```bash
kubectl create secret generic lighthouse-pg-url \
  -n lighthouse --from-literal=LIGHTHOUSE_PG_URL='postgresql://…'

kubectl create secret generic lighthouse-env -n lighthouse \
  --from-literal=LIGHTHOUSE_SECRETS_KEY=… \
  --from-literal=LIGHTHOUSE_ADMIN_TOKEN=… \
  --from-literal=OPENAI_API_KEY=…

helm upgrade --install lighthouse charts/lighthouse \
  -n lighthouse -f values.prod.yaml
```

After install, follow the post-`NOTES.txt` instructions (or run them
ahead of time) to apply the SQL migrations:

```bash
psql "$LIGHTHOUSE_PG_URL" -f web/sql/001_users.sql
psql "$LIGHTHOUSE_PG_URL" -f web/sql/002_importers.sql
psql "$LIGHTHOUSE_PG_URL" -f web/sql/003_importers_unique_name.sql
psql "$LIGHTHOUSE_PG_URL" -f web/sql/004_webhooks.sql
```

Full chart reference: [`../charts/lighthouse/README.md`](../charts/lighthouse/README.md).

## Postgres prerequisites

Lighthouse needs Postgres 14+ with the `pgvector` extension.

| Provider | Setup |
|---|---|
| [Neon](https://neon.tech) | Enabled out of the box. `CREATE EXTENSION vector;` is auto. |
| AWS RDS | Postgres 15+ supports pgvector. `CREATE EXTENSION vector;` once. |
| GCP Cloud SQL | Postgres 15+, same. |
| Azure Flexible | Postgres 14+, enable `vector` in *Server parameters → azure.extensions*. |
| [CloudNativePG](https://cloudnative-pg.io) | Use image `ghcr.io/cloudnative-pg/postgresql:17-pgvector`. |
| Self-host | Run `pgvector/pgvector:pg17` (what Compose uses). |

One-time DDL (the API also auto-creates `chunks` on first request,
but apply the rest yourself):

```sql
CREATE EXTENSION IF NOT EXISTS vector;
-- then run web/sql/001..004 as shown above.
```

## Backups + DR

Lighthouse persists everything in Postgres:

- `chunks` — the corpus (large; takes hours to re-ingest if lost)
- `importers` + `importer_runs` — operator-managed configs
- `webhooks` + `webhook_deliveries` — subscriptions + queue
- `users` + `usage_daily` + `paddle_events` — only if you run the SaaS frontend

Use your provider's PITR (Neon branches / RDS automated backups /
CNPG WAL-archiving). Lost data == re-ingest from upstream sources.

The Fernet key (`LIGHTHOUSE_SECRETS_KEY`) is the **second** thing to
back up. Losing it = every encrypted importer secret in the DB
becomes opaque garbage. Keep a copy in your password manager + your
infra-secret store.

## Upgrade

```bash
# Compose
docker compose pull && docker compose up -d

# Helm
helm upgrade --install lighthouse charts/lighthouse -n lighthouse -f values.prod.yaml
```

Both paths are non-destructive — the chunks table survives in-place
upgrades. Schema changes ship in `web/sql/00X_*.sql`; new migrations
are idempotent (`CREATE … IF NOT EXISTS`) and safe to re-apply.

## Monitoring

- `GET /health` — liveness (no DB hit; cheap).
- `GET /v1/corpus/stats` — readiness signal (DB query; if it hangs,
  your Postgres or migrations are wrong).
- Prometheus / OpenTelemetry: not wired yet. The next FU bumps in.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `/health` 200 but `/v1/corpus/stats` 500 | `LIGHTHOUSE_PG_URL` wrong, or `pgvector` extension missing in the DB. |
| Importer runs perma-stuck `running` | Pod restarted mid-drain. The API auto-sweeps on next boot — wait 30s. |
| `LIGHTHOUSE_SECRETS_KEY is not set` on importer save | Set the env var on the API pods. |
| Webhook never delivers | Check `/v1/webhooks/{id}/deliveries` — the row carries `last_status` + `last_error`. Verify your receiver returns 2xx. |
