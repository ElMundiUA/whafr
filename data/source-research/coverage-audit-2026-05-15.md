# Lighthouse corpus coverage audit — 2026-05-15

Five sub-agents probed the deployed MCP at
`https://lighthouse.harborgang.com/mcp/` with 128 read-only queries
across five SDLC role surfaces. This is the consolidated picture; it
exists to drive the next ingest cycle.

## Headline

| Domain | Queries | Gap-rate | Avg useful / 5 |
|---|---:|---:|---:|
| Engineering practices | 22 | 64 % | 1.6 |
| Architecture & patterns | 30 | **93 %** | 0.1 |
| DevOps & infra | 30 | **87 %** | 0.23 |
| Security + Frontend/UX | 20 | 75 % | 1.0 |
| PM & planning | 26 | 77 % | 0.5 |
| **Total** | **128** | **~79 %** | **~0.7** |

A query is "useful" when at least one of the top-5 hits substantively
answers the question; brand name-drops without content do not count.

## What works

- Cucumber / BDD / Gherkin (4–5/5 useful)
- Carbon + Fluent design tokens, ARIA, WCAG a11y basics
- PRD template (Atlassian Confluence)
- Story points / Planning Poker (Cohn)
- Test pyramid
- OpenTelemetry + Jaeger surface (partial — no Prometheus/SLO math)

## Catastrophic holes

- Distributed systems — CRDT/OT, CAP, consensus (Raft/Paxos),
  idempotency / exactly-once, event sourcing, CQRS, saga, outbox,
  circuit breaker, bulkhead. 0/30 useful hits.
- Kubernetes runtime — probes, HPA, PDB, NetworkPolicy, RBAC,
  StatefulSet vs Deployment, init containers, pod lifecycle. 0/8.
- IaC — Terraform state/locking/drift, Helm, Kustomize, Pulumi. 0/4.
- Auth & threat modelling — OAuth 2.1/PKCE, magic-link, STRIDE,
  secret rotation, GDPR/SOC2/HIPAA. 0–1/15.
- Product strategy + discovery — Cagan, Torres, JTBD, Wardley
  mapping, 7 Powers, Blue Ocean. 0/many.
- Modern PM frameworks — RICE, WSJF, Kano, ICE. 0/6.
- Core Web Vitals, React Suspense / Server Components, prefetch.
  0/few.
- Modern testing — mutation (Stryker), contract (Pact), property-
  based (Hypothesis), snapshot. 0/4.

## Ingest bug (likely systematic)

The Security agent observed that OWASP cheat-sheet **titles** are
indexed but the **article bodies** are missing — the sitemap crawler
seems to have walked the index/navigation page without fetching each
cheat-sheet's actual content. This is a candidate for a systemic
issue worth investigating before re-ingest. See companion log in
``coverage-audit-2026-05-15-owasp-bug.md`` if/when filed.

## Prioritised seed list

### Phase 1 — most leverage (5 sources, closes ~30 gaps)

| Source | What it closes |
|---|---|
| ``martinfowler.com/bliki/`` + ``/eaaDev/`` + ``/refactoring/catalog/`` | TDD, refactoring catalog, ES/CQRS, Saga, Circuit Breaker, TechDebtQuadrant, FeatureToggle, TrunkBasedDevelopment, TestPyramid |
| ``microservices.io/patterns/`` (Chris Richardson) | saga, outbox, BFF, API gateway, idempotent consumer |
| ``kubernetes.io/docs/concepts/`` | probes, HPA, PDB, NetworkPolicy, RBAC, StatefulSet, init, lifecycle |
| ``cheatsheetseries.owasp.org/`` (with body content) | SQLi, XSS, CSRF, SSRF, JWT, CORS, secrets management |
| ``sre.google/sre-book/`` + ``/workbook/`` | SLO/SLI/error budget math, burn-rate alerting |

### Phase 2 — high-priority single-domain (8 sources)

| Source | Domain |
|---|---|
| ``crdt.tech`` + ``docs.yjs.dev`` + Automerge docs | CRDT/OT/presence |
| ``producttalk.org`` + ``svpg.com/articles/`` | Discovery (Torres + Cagan) |
| ``argo-rollouts.readthedocs.io`` | Progressive delivery |
| ``web.dev/learn/performance/`` + ``/articles/vitals`` | Core Web Vitals, RSC perf |
| ``external-secrets.io`` + ``github.com/bitnami-labs/sealed-secrets`` | K8s secret sync |
| ``developer.hashicorp.com/terraform/`` | IaC patterns + state |
| ``learnwardleymapping.com`` + ``christinawodtke.com`` | Strategy + OKRs |
| ``lennysnewsletter.com/archive`` + ``amplitude.com/blog`` | Modern PM metrics |

### Phase 3 — niche fills (10 sources)

- ``oauth.net/2.1/`` — OAuth canon
- ``aphyr.com/posts`` (Jepsen) + Raft paper — consensus, linearizability
- ``humanizingwork.com/.../splitting-user-stories/`` + ``agileforall.com`` — INVEST
- ``trunkbaseddevelopment.com`` + ``launchdarkly.com/blog`` — TBD + flags
- ``stryker-mutator.io`` + ``docs.pact.io`` + ``hypothesis.readthedocs.io`` — modern testing
- ``ui.shadcn.com/docs`` + ``react.dev/reference/react`` — shadcn + React 19
- ``redis.io/docs/manual/patterns/`` + RFC 9111 — caching/CDN
- ``itamargilad.com`` — GIST planning
- ``intercom.com/blog/product-management/`` — PM patterns
- ``gdpr-info.eu`` + AICPA SOC2 TSC — compliance basics

## How this report should be used

It is **input for the harvester** — the single sanctioned write path
into the graph. The list above is intentionally URL-shaped so it can
be folded directly into a ``runner.yaml`` source spec. Do not
``propose`` from the agent side.
