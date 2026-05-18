# Lighthouse — content & design audit, V2

V1 treated `Lighthouse` as one product with three deployment options. It
isn't. Two products on different price curves, sold to different buyers,
shipping on different cadences. Everything below restructures the site
around that split. Voice unchanged: technical, no "unlock", no
"revolutionary", no "AI-powered"; champagne `#cfa96b` is the only accent.

SaaS-only merchandising numbers: **71K chunks · ~14K sources · 21 role
recipes · 98% summary coverage · audit mean useful 3.07/5**. These do not
appear on `/engine` — the engine ships empty by design.

---

## 1. Naming convention

| Option | SaaS | OSS | Verdict |
|---|---|---|---|
| **A — chosen** | `Lighthouse` | `Lighthouse Engine` | Wordmark untouched; "Engine" reads as substrate, not a competing brand |
| B | `Lighthouse` | `lighthouse-core` | "core" implies a Pro/enterprise core, wrong signal |
| C | `Lighthouse Cloud` | `Lighthouse` | Rebrands the live product, breaks current OG / link equity |
| D | Different brands | — | Doubles brand surface; throws away the name people already type |

**Pick: A.** Defence in three lines:

1. The hosted product is what people land on — it deserves the bare name.
2. "Engine" is the right metaphor: it ships empty, the operator fuels it.
3. Zero rebrand cost on the live SaaS; the OSS gets a clarifying noun.

Repo stays `github.com/ElMundiUA/lighthouse`. Never rename.

---

## 2. Information architecture

| Route | Audience | Notes |
|---|---|---|
| `/` | SaaS buyer (engineer using a coding agent) | Hero, search, SaaS-only stats, recently-indexed. Engine mentioned once, in a sub-search ribbon. |
| `/pricing` | SaaS buyer | Drop the self-host tile. Four real tiers. Single-line engine teaser at page bottom. |
| `/about` | Both audiences, umbrella view | Opens with umbrella + two-card split, then shared sections. |
| `/engine` **(new)** | Platform / infra engineer | OSS landing. Hero, repo, capabilities, deploy outline. **No corpus numbers.** |
| `/home` | Signed-in SaaS user | Unchanged purpose. SaaS dashboard. |
| `/chunk/[id]` | Anyone deep-linked from an agent | SaaS artefact, unchanged. |
| `/legal/*`, `/admin/*` | Boilerplate / internal | Out of scope. |

`/sections` stays gone (folded into home in V1).

### Header nav

| Position | Label | Target |
|---|---|---|
| 1 | Pricing | `/pricing` |
| 2 | Engine | `/engine` |
| 3 | About | `/about` |

Drop the `Home` entry — the wordmark is already the home link. `/engine` is
the new addition; `isActive` already handles non-overlap correctly.

Wordmark per page: default `Lighthouse.` (champagne period). On `/engine/*`,
swap to `Lighthouse Engine` — `Engine` rendered `text-mist/55 font-normal
ml-1`, no period. Same `<a>` wrapper, two label variants based on
`Astro.url.pathname`.

---

## 3. Per-page rewrite blocks

### `src/pages/index.astro` — SaaS home (major rewrite)

**Diagnosis.** Page sells engine and SaaS in the same column; buyers can't
tell whether $12/mo buys hosting or the corpus. "Three ways to run
Lighthouse" treats self-host as a third tariff.

**Cut.** Fourth stat card (`Apache-2.0`). "Three ways to run Lighthouse"
section. `Apache-2.0. Self-host or use the hosted index.` from hero subhead.
**Keep.** Hero, search bar, three real stat cards, recently-indexed table.
**Add.** Engine teaser ribbon below the search bar.

#### Rewrite — hero (anon, no `q`)

Two candidates in §5. Both drop the Apache-2.0 phrase.

#### Rewrite — sub-search ribbon

```
{stats.total_chunks} chunks · {stats.total_sources} sources · {stats.total_recipes} recipes · 30 free searches/day, no card
Engine is open source — github.com/ElMundiUA/lighthouse · /engine →
```

Both lines `text-xs text-mist/45 font-mono`. Line 2 is the only
above-the-fold engine surface.

#### Rewrite — stat cards (3-up, was 4-up)

| Card | Kicker | Value |
|---|---|---|
| 1 | Indexed chunks | `{stats.total_chunks}` |
| 2 | Distinct sources | `{stats.total_sources}` |
| 3 | Role recipes | `{stats.total_recipes}` |

Grid `md:grid-cols-3`. The github link moves to the ribbon and footer.

#### Rewrite — replace "Three ways to run Lighthouse"

Two-card block (the two SaaS plans) + a single-line engine mention below.
Not a third card.

```
Section kicker:  Two plans, one corpus

Card 1 — Free
  200 searches/day signed-in (30/day anon, per IP). Same corpus, same
  MCP endpoint, no card.   → Sign in

Card 2 — Pro
  1,500 searches/day. Top-30 results. Watch-on-update on bookmarked
  sources. Cross-encoder rerank. $12/mo or $9/seat for teams.
  → See pricing

Below the two cards, single dim line:
  Building your own knowledge layer? Lighthouse Engine is the same
  retrieval stack, Apache-2.0, with your corpus instead of ours. → /engine
```

#### Rewrite — search-results-mode header strip

Unchanged from V1.

#### Design tweaks

- Drop fourth stat card; grid → `md:grid-cols-3`.
- Replace the three-card block with the two-card "Two plans, one corpus" +
  dim engine line.
- Recently-indexed: Recipes cell shows 4 chips, right-aligned.
- Anon-with-results upsell strip between rank 4 and 5: unchanged from V1.

---

### `src/pages/pricing.astro` — kill the 5th tile, add engine teaser

**Diagnosis.** Self-host tile sits in the grid as a `$0` competitor to Pro;
buyers infer they get the curated corpus for free if they self-host. They
don't.

**Cut.** Fifth tile (`<!-- Self-host -->`). `Self-host for free…` from
`pageSubhead`.
**Keep.** Anon / Reader / Pro / Team, billing toggle, Paddle wiring.
**Add.** Single-line teaser strip below the grid linking to `/engine`.

#### Rewrite — page heading + subhead

```
Heading:   Pick a tier
Subhead:   Same corpus on every tier — IETF RFCs, OWASP, NIST SP-800,
           framework docs, methodology, post-cutoff release streams. Caps
           reset 00:00 UTC. Cancel anytime, prorated.
```

#### Rewrite — engine teaser strip (new, end of page)

See §6 for the three tonal variants. Render as centred `max-w-2xl`
`border-y border-white/5 py-6`, `text-mist/65`, `/engine` link champagne. No
card, no glass, no button.

#### Design tweaks

- Delete fifth `<article>`. Grid → `md:grid-cols-2 lg:grid-cols-4`.
- The Self-host concierge line (`Need quarterly source curation? Talk to us.`)
  moves to `/engine`.
- If the V1 comparison table ships, drop the "Source code" row.
- Annual savings literal (`Save $36/yr`) stays.

---

### `src/pages/about.astro` — restructure under the umbrella

**Diagnosis.** Reads "the engine is open and we host it too" — wrong frame.
Right frame is "two products under one name; here's the difference." The
single centred column also kills scannability.

**Cut.** `<article class="max-w-2xl chunk-prose">` flat layout.
**Keep.** Bodies for `Why a finder…`, `How retrieval works`, `How it differs
from Context7` — accurate and on-voice.
**Add.** Umbrella opener and a two-card split before retrieval mechanics.

#### Rewrite — page heading + subhead

```
Heading:   What is Lighthouse?
Subhead:   Two products sharing one engine. A hosted SaaS for engineers who
           want their coding agent grounded in canonical sources, and an
           open-source engine for teams that want to run their own.
```

#### Rewrite — section order

1. The umbrella _(new)_ · 2. Two-card split _(new)_ · 3. Why Lighthouse
exists _(SaaS gap)_ · 4. Why a finder, not a republisher _(both products)_ ·
5. How retrieval works _(shared)_ · 6. What's indexed on the SaaS _(71K /
14K / 21 live here)_ · 7. How it differs from Context7 · 8. Pricing or
self-host _(routes the reader)_

#### Rewrite — section bodies (deltas only)

**The umbrella** _(new)_
> Lighthouse is two products. **The SaaS** (this site) is a grounding layer
> for coding agents — we run the index, you point your agent at the MCP
> endpoint, your agent stops hallucinating RFC numbers. **The Engine** is the
> retrieval stack underneath, open-source under Apache 2.0, for platform
> teams that want a private knowledge layer with their own corpus. Same
> code, two corpora: ours and yours.

**Two-card split** _(new — visual element, not prose)_

| | Lighthouse (SaaS) | Lighthouse Engine (OSS) |
|---|---|---|
| Kicker | Hosted product | Open-source engine |
| One-line | Curated SDLC corpus served over MCP | Run your own retrieval stack on your own corpus |
| Audience | Engineers using coding agents | Platform / internal-tools / AI infra teams |
| Price | Free 200/day · Pro $12/mo · Team $9/seat | Free · Apache-2.0 · your hardware |
| Corpus | 71K chunks · 14K sources · 21 recipes (curated by us) | Empty; you bring sources + recipes |
| CTA | See pricing → | github.com/ElMundiUA/lighthouse → |

Each card `glass p-6`. SaaS card keeps a champagne ring; engine card unringed.

**Why Lighthouse exists** — unchanged from V1.

**Why a finder, not a republisher** — keep current copy.

**How retrieval works** — keep V1 copy. Tighten the audit-coverage callout to
read "On the hosted index, weekly audit mean is 3.07/5…" so the number's
SaaS-scope is unambiguous.

**What's indexed on the SaaS**
> The hosted index pulls from four buckets — canonical standards (RFCs,
> OWASP, NIST SP-800, MITRE ATT&CK), Tier-1 framework docs, practitioner
> literature, and post-cutoff streams. 21 role recipes assemble those into
> per-role rosters: Developer, DevOps, Security, ML, SRE, Testing, Mobile,
> Architecture, Designer, Reviewer, Planning, Decomposition, Clarification,
> PM, Data Eng, Embedded, Web3, Gamedev, Performance, Network, Self-heal.
> Self-host gets none of this — engine ships empty.

**How it differs from Context7** — keep verbatim.

**Pricing or self-host**
> Want our curated corpus? That's the SaaS — anon 30/day per IP, signed-in
> free 200/day, Pro 1,500/day for $12/mo. See [pricing](/pricing). Want the
> engine itself? See [`/engine`](/engine) — Apache-2.0, your corpus, your
> hardware.

#### Design tweaks

- Replace `max-w-2xl chunk-prose` with `md:grid-cols-[14rem_1fr]` — sticky
  TOC left, prose right at `max-w-2xl text-mist/80 leading-relaxed`.
- Two-card split (section 2) breaks out to full width (`md:grid-cols-2 gap-5`).
- Headings get champagne underlines on `:target`. Drop `chunk-prose`.

---

### `src/pages/engine.astro` — new page, full draft

**Diagnosis.** No page exists; the engine has zero dedicated surface, so
pricing and home end up doing its job and confusing the buyer.

**Audience.** Platform engineers, internal-tools teams, AI infra companies
wanting a private retrieval layer. They care about license, capabilities,
deploy, BYO-corpus. Not $12/mo. **Constraint:** no `71K / 14K / 21 / 98% /
3.07` on this page. Engine ships empty.

#### Section structure

1. Hero · 2. What's in the box (4-up) · 3. Engine vs. SaaS callout ·
4. Deploy (3-step) · 5. Bring your own corpus · 6. License + Support

#### Hero

```
Kicker:    Open-source retrieval engine

H1:        Run your own grounding layer.
                ^^^^^^^^^^^^^^^^^^^^^^^^   (champagne on "grounding layer")

Subhead:   Lighthouse Engine is the retrieval stack behind
           lighthouse.harborgang.com — ingest pipeline, chunker, summariser,
           BM25 + pgvector retrieval, cross-encoder reranker, MCP server,
           admin panel. Apache-2.0. Bring your own corpus.

CTAs:      [ View on GitHub ↗ ]   [ Deployment guide → ]
           (primary champagne)    (secondary, links repo README#deploy)

Sub-hero dim mono line:
           ~50 MB image · runs on a $20/mo VPS · Postgres + pgvector required
```

#### What's in the box (4-up)

| Card | Kicker | Body |
|---|---|---|
| 1 | Ingest pipeline | Sitemap + trafilatura crawler, hash-based delta-ingest, role-tagged chunkers. YAML source manifests; one cron per role. |
| 2 | Indexer | BM25 over weighted tsvector (summary + keywords A, tags B, content C) plus pgvector cosine on chunk embeddings. Async summary worker via OpenRouter or local LFM2. |
| 3 | Reranker | Cross-encoder rerank on top-N candidates. Reciprocal-rank-fusion merge. Pluggable model. |
| 4 | MCP server | `search` and `fetch_source` tools out of the box. Token-gated, rate-limited per subject. Admin panel for source review. |

All `glass p-5`. Champagne kicker. No per-card CTAs; the hero already has the
two real ones.

#### Engine vs. SaaS (callout, `max-w-2xl mt-10`)

```
Section kicker:  Engine vs. SaaS

The hosted Lighthouse at lighthouse.harborgang.com is the engine plus
**our** corpus — 71K chunks of canonical SDLC reference, 14K sources, 21
role recipes, refreshed by our ingest crons. The engine itself ships empty.
You bring sources, you bring recipes, you run the crons. If you want the
curated corpus, that's the SaaS — see /pricing.
```

Only place on `/engine` the SaaS numbers may appear, and only in explicit
contrast.

#### Deploy (3-step)

Three numbered code blocks, mono, dark. Each `bg-black/40 border
border-white/10 rounded-xl p-4 font-mono text-sm`. Step numbers champagne,
large, left of each block.

```
1. Clone and pull the image
   git clone https://github.com/ElMundiUA/lighthouse
   docker pull ghcr.io/elmundiua/lighthouse:latest
2. Provision Postgres + pgvector, point the engine at it
   DATABASE_URL=postgres://...
   docker run -e DATABASE_URL ... ghcr.io/elmundiua/lighthouse
3. Drop YAML source manifests into ./recipes/ and run the ingest
   docker exec lighthouse python -m lighthouse.ingest --role security

Full guide: github.com/ElMundiUA/lighthouse#deploy →
```

#### Bring your own corpus

```
Heading:    Bring your own corpus
Subhead:    The engine doesn't pick what's canonical for your team — you do.

A "recipe" is a YAML manifest listing sources (URLs, sitemaps, RSS feeds,
GitHub repos), a role tag, and a refresh cadence. The ingest cron crawls
each source, hashes the result, chunks new content, embeds and summarises,
writes to Postgres. Your private repos, internal runbooks, vendor docs —
whatever you'd want a coding agent grounded on. Recipes are versioned with
the engine; the SaaS uses the same format internally.
```

#### License + Support

Two-card row, `glass p-5` each.

| Card | Kicker | Body | Link |
|---|---|---|---|
| 1 | Apache-2.0 | Use commercially, modify, redistribute, sublicense. No patent-grant gotchas. | LICENSE on GitHub → |
| 2 | Community + by-arrangement | Issues and PRs on GitHub. For stand-up help, source curation, or private-corpus consulting, email us — we'll quote a one-off engagement. | lighthouse@harborgang.com |

#### Design tweaks

- Same `Site.astro` layout. Page passes `productScope="engine"`,
  `title="Lighthouse Engine — open-source retrieval for grounded coding agents"`,
  `ogImage="/og-engine.png"`, `hideUsagePill={true}`. Wordmark renders
  `Lighthouse Engine` (see §7).
- Below standard footer, single centred line: `Looking for the hosted
  product? → lighthouse.harborgang.com` (`text-xs text-mist/45`).

---

### `src/pages/home.astro` — minor touch

**Diagnosis.** Dashboard is correctly SaaS-scoped. Only remaining bleed is
`the lighthouse` (lowercase, definite article) in the subhead and the
Claude-Code caption.

**Changes.** Replace both `the lighthouse` literals with `Lighthouse`. Add
one line at the bottom of the "More" footer block:

```
- Lighthouse Engine — the OSS stack this dashboard runs on   (→ /engine)
```

Signed-in users are the most likely self-hosting audience; right place for
an engine cross-link. Keep all V1 design tweaks for this page (upgrade CTA
as a real button, copy-button on MCP endpoint aside, etc.). No structural
change.

---

### `src/layouts/Site.astro` — nav, footer, defaults

**Diagnosis.** Global title applies SaaS positioning to every page; nav has
no engine entry; the `Build` footer column over-indexes on engine concepts
on SaaS pages.

#### Nav

```
nav = [
  { href: "/pricing", label: "Pricing" },
  { href: "/engine",  label: "Engine"  },
  { href: "/about",   label: "About"   },
];
```

Drop the `Home` entry — wordmark already handles it.

#### Wordmark per page

```
Default:           Lighthouse<span class="text-champagne">.</span>
On /engine/*:      Lighthouse<span class="text-mist/55 font-normal ml-1">Engine</span>
```

Branch on `Astro.url.pathname.startsWith("/engine")`. Same `<a>` wrapper.

#### `productScope` prop (new)

`Site.astro` exposes `productScope: "saas" | "engine"` (default `"saas"`).
Swaps the default `<title>` and `<meta description>`:

| Scope | Default title | Default description |
|---|---|---|
| `saas` | `Lighthouse — grounding layer for coding agents` | `Lighthouse indexes 71K chunks of canonical SDLC reference — RFCs, OWASP, NIST, framework docs — served to coding agents over MCP. Free 200/day, Pro $12/mo.` |
| `engine` | `Lighthouse Engine — open-source retrieval for grounded coding agents` | `Lighthouse Engine is the open-source retrieval stack behind Lighthouse. Ingest pipeline, chunker, summariser, BM25 + pgvector, cross-encoder rerank, MCP server. Apache-2.0. Bring your own corpus.` |

Per-page `title` overrides still apply.

#### Footer — three columns (was four)

| Column | Header | Links |
|---|---|---|
| 1 | Lighthouse | Sign in · Pricing · About · Recently indexed |
| 2 | Engine | GitHub · MCP endpoint · REST API · Deployment guide |
| 3 | Harbor Gang | Contact · Privacy · Terms · Cookies · `Harbor Gang · Kyiv & remote EU` |

`Build / Read / Legal / Contact` becomes a product-line split. `MCP
quick-start` (signed-in SaaS feature on `/home`) lives in column 1; the raw
MCP endpoint URL (protocol surface, equally relevant to engine operators)
stays in column 2.

Copyright line unchanged: `© {year} Harbor Gang. Built in the open at
github.com/ElMundiUA/lighthouse.`

#### Design tweaks

- Hide the sticky usage pill on `/engine/*` via `hideUsagePill` — cap is
  SaaS-only, otherwise it's a confusion vector.
- On `/engine/*`, swap the navbar `Sign in · free, no card` CTA for a
  secondary `View on GitHub ↗` button. Engine audience converts to "star",
  not "sign up".

---

## 4. OSS surfacing on SaaS pages — two placements, no more

1. **Above the fold on `/` — sub-search ribbon line 2.** One line, mono, dim:
   `Engine is open source — github.com/ElMundiUA/lighthouse · /engine →`.
   Doesn't compete with the search bar; gives the skim reader a reason to
   know the open thing exists.
2. **Footer column 2 — "Engine".** GitHub, MCP endpoint, REST API, Deployment
   guide. Anyone reaching the footer who's curious finds the engine.

What we do **not** do: engine mentioned as a tile on `/pricing` (the §6
teaser is a one-liner, not a card); engine in the cap-pill, rate-limit
banner, recently-indexed caption, or `/chunk/[id]`; an "open source!" badge
on every page.

---

## 5. Hero copy

### SaaS `/` — Candidate A (gap-closer)

Buyer thinks: "my agent hallucinates and I want to fix it." Names the
failure mode. Strong if the buyer's already felt it; weaker if not.

```
Kicker:    Grounding for coding agents
H1:        Stop your agent inventing RFC numbers.   (champagne on "RFC numbers")
Subhead:   Lighthouse plugs into Claude Code, Claude Desktop, Cursor, and any
           other MCP-capable agent, and answers retrieval queries from 71,000
           chunks of canonical SDLC reference. Cited answers, not confident
           paraphrases.
```

### SaaS `/` — Candidate B (capability)

Buyer thinks: "I want my small/medium model competitive on grounded answers."
Frames Lighthouse as a model upgrade for cheaper coding models. Strongest
for the Qwen-Coder / DeepSeek-Coder audience; weaker on "I just use Claude".

```
Kicker:    The corpus your coding agent doesn't have
H1:        Frontier-grade grounding for any coding agent.   (champagne on "grounding")
Subhead:   71,000 chunks of canonical SDLC reference — RFCs, OWASP, NIST,
           framework docs, post-cutoff release streams — served over MCP.
           Closes the gap between mid-tier models and frontier on grounded
           technical questions.
```

### Engine `/engine` — single candidate

Buyer thinks: "I need a private knowledge layer for our agents." No corpus
numbers; names the SaaS as existence-proof; license and posture in two lines.

```
Kicker:    Open-source retrieval engine
H1:        Run your own grounding layer.   (champagne on "grounding layer")
Subhead:   Lighthouse Engine is the retrieval stack behind
           lighthouse.harborgang.com — ingest pipeline, BM25 + pgvector
           retrieval, cross-encoder rerank, MCP server. Apache-2.0. Bring
           your own corpus.
```

---

## 6. Pricing engine teaser strip — three variants

All three: single paragraph, centred, `max-w-2xl`, `border-y border-white/5
py-6`, `text-mist/65`, `/engine` link champagne.

- **Terse** — `Want the engine itself? It's open source — /engine.`
- **Explanatory** — `The pricing above buys access to our hosted corpus. If
  you want the engine instead — same retrieval stack, your own sources, your
  own hardware, Apache-2.0 — that's a different product. See /engine.`
- **Sales-pitched** — `Self-hosting? Lighthouse Engine is the same retrieval
  stack we run, free under Apache-2.0, ready for your private corpus. Built
  for platform teams, not individual engineers. → /engine`

**Recommendation: Explanatory.** Does the disambiguation work the page
exists to do. Terse assumes too much; sales-pitched reads like a competing
tier — the exact failure mode this rework kills.

---

## 7. Cross-cutting refresh

### OG images — two, not one

One shared card risks the same product-conflation the rest of the site has.

| Field | `/public/og-saas.png` | `/public/og-engine.png` |
|---|---|---|
| Background | Ink (`#0b0e12`) with the existing champagne radial top-centre | Ink, no radial — flat, slightly darker |
| Wordmark | `Lighthouse.` (champagne period), centre, 96pt display | `Lighthouse Engine` — `Lighthouse` mist, `Engine` champagne, 88pt display |
| Subline | `grounding layer for coding agents` — 28pt mist | `open-source retrieval engine` — 28pt mist |
| Footer strip | `71K chunks · 14K sources · 21 recipes · MCP-ready` — 18pt mist/55 tabular | `Apache-2.0 · BM25 + pgvector · MCP server · self-host` — 18pt mist/55 |
| URL pill, bottom right | `lighthouse.harborgang.com` | `github.com/ElMundiUA/lighthouse` |
| Size | 1200×630 PNG | 1200×630 PNG |

Current `og.png` becomes `og-saas.png`; `og.svg` stays as the shared source.
Pass via `ogImage` per page; `Site.astro` default = `og-saas.png`.

### Default `<title>` — different per scope

Two defaults, swapped by `productScope` (table in §3 `Site.astro` block).

### Footer — restructured

From four columns (`Build / Read / Legal / Contact`) to three along the
product line:

| Column | Header | Links |
|---|---|---|
| 1 | Lighthouse | Sign in · Pricing · About · Recently indexed |
| 2 | Engine | GitHub · MCP endpoint · REST API · Deployment guide |
| 3 | Harbor Gang | Contact · Privacy · Terms · Cookies · `Kyiv & remote EU` |

Puts the two-product split into the most-visible chrome on every page —
the whole point of this audit.

---

## Asset / copy quick-wins (everywhere this rework touches)

- Strip every remaining `the lighthouse` (lowercase, definite article).
- OSS is `Lighthouse Engine` — two words, both capitalised. Never
  `lighthouse-engine`; never bare `Engine` in body copy.
- SaaS is `Lighthouse` — single proper noun. Never `Lighthouse Hosted`,
  `Lighthouse Cloud`, or `Lighthouse SaaS` in user-facing copy.
- Repo is `github.com/ElMundiUA/lighthouse`. Don't add `-engine`.
- `Apache-2.0` references appear on `/engine` and on `/about` under the
  engine card. Not on `/` hero, `/pricing` tiers, or `/home`.
- `Self-host` as a phrase: only on `/engine` and the `/pricing` teaser strip.

---

## Out of scope (flagged)

- `chunk/[id].astro`, trust block, conversion-funnel inline strips, sticky
  TOC component for `/about`, GitHub-star badge on `/engine` hero, Roadmap /
  Changelog pages — V1 proposals stand or revisit later.
