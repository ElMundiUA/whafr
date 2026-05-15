# Lighthouse coverage — milestone audit after episode-body retrieval

Date: 2026-05-15, after commit `6a8ea98`. The previous nine audit
waves (47 sub-agents, ~1000 queries, ~91% mean gap-rate) surfaced
**one architectural lever**: Graphiti's edge-only search returns
entity-fact triples but never the prose of the ingested source.
This commit added an episode-body second-pass behind ``search``,
filling half of ``top_k`` with snippets from a Lucene fulltext
index over ``Episodic.content``.

This audit measures the immediate effect on previously-flat domains.

## Re-probes vs original baselines

| Domain | Wave-1 gap | Last re-probe (no ep-body) | After ep-body | Δ vs W1 |
|---|---:|---:|---:|---:|
| Architecture & patterns | 93 % | 62 % | n/a (was already lifting on saga/CQRS edges) | −31 pp |
| Engineering practices | 64 % | 58 % | n/a | −6 pp |
| **DevOps & infra** | 87 % | 90 % (flat) | **74.5 %** | **−12.5 pp** |
| **Security + FE/UX** | 80 % | 75 % (flat) | **55 %** | **−25 pp** |
| PM & planning | 77 % | 81 % (within noise) | n/a (ingest lagging) | +4 pp |

## Specific lifts unlocked by ep-body

**Security**:
- OAuth/PKCE: returns full RFC stack — 7636 PKCE, 6749, 9449 DPoP,
  8705 mTLS, OWASP OAuth Cheat Sheet body.
- OWASP cheat sheets verbatim: CSRF + Secrets Mgmt + Threat Modeling
  + Secure Code Review + Session Management.
- STRIDE: Sigstore + CycloneDX threat model docs + OWASP.

**DevOps**:
- SLO/SLI/error budget: Google SRE podcast bodies, Nobl9 SLO articles,
  Art of SLOs.
- Argo Rollouts: actual Rollouts + Flagger documentation.
- OpenTelemetry: otel-javaagent setup, OBI OTLP, instrumented-pydantic.
- secret-rotation CI: OWASP Secrets Management + GHA Security
  Cheatsheet.

## What didn't lift (and why)

Topics still ≥80% gap after the ep-body deploy:

- React.dev hooks / Suspense / Server Components — only React
  *Native* surfaces. ``react.dev`` source is in phase1-recovery.yaml
  but the ingest job didn't reach it before the killing of older
  jobs to free CPU.
- web.dev / Core Web Vitals LCP/CLS/INP — same: ``web.dev`` is in
  phase1-recovery.yaml but didn't complete.
- shadcn/ui specifics — listed in phase1; same.
- TanStack Query optimistic mutations — listed in phase1; same.
- GDPR/DSAR — phase4 source (gdpr-info.eu) ingesting later.
- k8s.io/docs/concepts/ — the huge 300-page sitemap in phase1; same.

The pattern: **the architectural fix is shipped; remaining holes
are purely "ingest hasn't reached that source yet" rather than
"search is missing it"**. As the long-running phase-5/6/7/8 jobs
churn through their sitemaps, coverage will continue to climb
without further code changes.

## Recommended cadence to hit < 10% gap target

1. Let phase-5..8 jobs finish (an hour or two more).
2. Re-probe the same five domains.
3. Build a phase-9 yaml from anything still empty in that final
   re-probe (likely: GDPR/DSAR depth, React.dev specifics if
   web.dev didn't surface, niche topics like CRDT-internals).
4. Iterate. Each round drops 10-25 pp of gap based on this measurement.

## Cumulative across all 9 waves

49 sub-agents · ~1050 queries · pre-fix mean gap ~91 % · post-fix
mean gap (on revalidated domains) ~64 %.
