# Lighthouse Helm chart

Install Lighthouse Engine into a Kubernetes cluster. The chart ships
the API deployment + service + optional ingress + optional bundled
Astro admin UI. Postgres is **not** bundled — bring your own with
`pgvector` enabled.

## Install

```bash
helm install lighthouse charts/lighthouse \
  --namespace lighthouse --create-namespace \
  --set postgres.url=postgresql://user:pw@pg.svc:5432/lighthouse \
  --set env.secretsKey="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
  --set env.adminToken="$(openssl rand -base64 32)" \
  --set env.openaiKey=$OPENAI_API_KEY
```

Production deploys: keep secrets out of the CLI by pre-creating a
Secret and referencing it via `--set existingEnvSecret=lighthouse-env`.

## Postgres prerequisites

Lighthouse needs Postgres 14+ with the `pgvector` extension. Options:

| Provider | Notes |
|---|---|
| Neon | pgvector available out of the box. Easiest. |
| RDS / Cloud SQL / Azure | Enable extension once: `CREATE EXTENSION vector;` |
| CloudNativePG | `imageName: ghcr.io/cloudnative-pg/postgresql:17-pgvector` |
| Self-host | Run a `pgvector/pgvector:pg17` container or build your own. |

Once you have a database URL, apply the migrations:

```bash
psql "$DB_URL" -f web/sql/001_users.sql
psql "$DB_URL" -f web/sql/002_importers.sql
psql "$DB_URL" -f web/sql/003_importers_unique_name.sql
psql "$DB_URL" -f web/sql/004_webhooks.sql
```

The API also auto-creates its own `chunks` table on first request.

## Common overrides

```yaml
# values.prod.yaml
image:
  tag: "0.1.0"               # pin instead of `latest`

postgres:
  existingSecret: lighthouse-pg-url   # carries LIGHTHOUSE_PG_URL

existingEnvSecret: lighthouse-env    # carries secretsKey/adminToken/...

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: lighthouse.acme.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: lighthouse-tls
      hosts: [lighthouse.acme.com]

resources:
  requests: { cpu: 250m, memory: 1Gi }
  limits:   { cpu: 2,    memory: 4Gi }

web:
  enabled: true              # bundle the Astro admin UI
  ingress:
    enabled: true
    className: nginx
    hosts:
      - host: lighthouse-admin.acme.com
        paths:
          - path: /
            pathType: Prefix
```

```bash
helm upgrade --install lighthouse charts/lighthouse \
  -n lighthouse -f values.prod.yaml
```

## Upgrade / rollback

```bash
helm upgrade lighthouse charts/lighthouse -n lighthouse -f values.prod.yaml
helm rollback lighthouse <revision> -n lighthouse
```

The chart is forwards-compatible across patch versions; minor bumps
may introduce new optional values (always safe to upgrade), major
bumps are documented in the release notes.

## Uninstall

```bash
helm uninstall lighthouse -n lighthouse
```

The chart owns the Deployments + Services + Secrets it created. Your
Postgres data is **not** touched — that's your responsibility.

## See also

- [`../../docs/deployment.md`](../../docs/deployment.md) — side-by-side compose vs helm walkthrough.
- [`../../docs/getting-started.md`](../../docs/getting-started.md)
