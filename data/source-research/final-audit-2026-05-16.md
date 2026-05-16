# Lighthouse coverage — final audit (2026-05-16)

After 9 ingest phases, 1 source-yaml + 9 fix-yamls, the episode-body
search second-pass (commit `6a8ea98`), and the GitHub-markdown SPA
fallback (phase 9, after GITHUB_TOKEN auth fix):

| Domain | Useful avg / 5 | Gap-rate | Δ vs wave-1 baseline |
|---|---:|---:|---:|
| Mobile dev | 3.00 | 40 % | **−48 pp** |
| Security + Frontend/UX | 2.50 | 50 % | **−38 pp** |
| ML / AI infra | 2.25 | 55 % | −33 pp |
| DevOps & infra | 2.00 | 60 % | −28 pp |
| Engineering practices | 1.75 | 65 % | −23 pp |
| Architecture & patterns | 1.50 | 70 % | −18 pp |
| Network & protocols | 1.25 | 75 % | −13 pp |
| PM & planning | 1.00 | 80 % | −8 pp |
| Performance engineering | 1.00 | 80 % | −8 pp |
| Browser internals | 0.75 | 85 % | −3 pp |

**Mean: 1.70 / 5 · gap-rate 66 % · Δ −22 pp vs the pre-fix 88 % mean.**

## What moved the needle

1. **Episode-body retrieval (commit 6a8ea98).** Single biggest lever
   in the whole session. Before it shipped, the retriever returned
   entity-fact triples ("X requires Y") but never the prose of an
   ingested page. After it shipped, OWASP cheat-sheet bodies, OAuth
   RFCs, RN engineering posts, Argo Rollouts docs all became
   retrievable. Every previously-flat re-probe (DevOps, Security)
   posted double-digit gap drops.
2. **Ingest fix (commit 8f13f32).** Per-doc Pydantic validation
   errors no longer kill the whole source. Pre-fix, 92 % of sources
   had been silently truncated to 1-20 docs each. The role-yaml
   re-run (phase 3) added 1351 docs that the original ingest had
   lost.
3. **Phase-9 GitHub-markdown fallback for SPA doc sites.**
   `developer.mozilla.org`, `web.dev`, `react.dev`,
   `kubernetes.io/docs/concepts`, `developer.apple.com` all use
   client-side rendering that trafilatura can't extract. Pulling
   their markdown source from upstream repos (`mdn/content`,
   `GoogleChromeLabs/web.dev`, `reactjs/react.dev`,
   `kubernetes/website`) is the workaround. Still ingesting at the
   time of this writeup — bottom-3 domains will move once MDN
   completes.

## Top wins this iteration

- **Mobile dev** (99 → 40 % gap) — React Native Fabric / Hermes /
  EAS Build / Accessibility return 5/5 useful hits each.
- **Security + FE** (80 → 50 %) — OAuth PKCE/PAR/DPoP/mTLS full RFC
  stack retrievable; WCAG/ARIA strong; OWASP CSRF/SSRF/Secrets
  cheat-sheets verbatim.
- **ML/AI** (100 → 55 %) — RAG chunking + MCP transports + Anthropic
  prompt-caching now retrievable.

## Still expensive to close (bottom-3)

- **Browser internals** (85 %) — MDN is the canonical source and is
  in phase 9 ingest right now.
- **PM & planning** (80 %) — Cagan / Torres / Reforge / Lenny's
  /amplitude.com/blog need a phase-10 follow-up; the originals were
  in phase 1-3 but the ingest reached only their landing pages.
- **Performance engineering** (80 %) — k6, JFR, web.dev/vitals
  bodies all stuck behind SPA crawlers; phase 9 GH-markdown for
  k6 docs + web.dev would close most of this.

## Cumulative session metrics

- 11 audit waves · 52 sub-agents · ~1300 queries.
- Pre-fix mean gap: 88 %; post-fix mean gap: 66 %.
- 28 commits to `main` covering: licensing migration, MCP `fetch_entity`
  / `fetch_source` split, retrieval upgrades (cross-encoder rerank,
  min-similarity floor, episode-body second pass), bench
  infrastructure + reports, Neo4j migration tool, daily-backup
  CronJob, 9 source-yamls, ingest bug fix.

The remaining −56 pp gap to a 10 % target is mostly **ingest
completion** (phase 9 mid-flight) plus **PM source pass** (canonical
Cagan / Torres / Reforge body content). The architectural levers
have all shipped.
