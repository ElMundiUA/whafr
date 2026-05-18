# Lighthouse — content & design audit

A page-by-page rewrite pack. Voice: confident, technical, no marketing slop, no
"unlock", no "revolutionary", no "AI-powered". Champagne (`#cfa96b`) is the
single accent.

Real numbers to merchandise everywhere: **71K chunks · ~14K sources · 21 role
recipes · 98% summary coverage · audit mean useful 3.07/5 · Apache-2.0**.

---

## `src/pages/index.astro` — home + inline search

**Diagnosis.** Copy: hero is clear-ish ("find the canonical doc") but it sells
*search results*, not the *agent-grounding* job; the three-column "What /
For agents / For readers" is the weakest moment — generic, OSS absent. Design:
the four stat cards include "MCP endpoint" as a stat (it's not a stat, it's a
copy-paste target), recently-indexed table is the second-strongest moment but
hidden below the fold, and there's no upgrade CTA anywhere above the fold for
anonymous visitors hitting the cap.

### Rewrite — hero (anon, no `q`)

```
Kicker:    Grounding layer for coding agents

H1:        Stop your agent inventing RFC numbers.
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^   (champagne on "RFC numbers")

Subhead:   Lighthouse indexes the canonical SDLC corpus — IETF RFCs, OWASP,
           NIST SP-800, framework docs, practitioner literature, post-cutoff
           release streams — and serves it to your agent over MCP. Search
           returns a one-line fact plus a link to the original source.
           Apache-2.0. Self-host or use the hosted index.

Below the search bar (small, two-line):
           // 71,000 chunks across 14,000 sources · 21 role recipes
           // 30 free searches/day anon — no card. `/pricing` for more.
```

### Rewrite — "Three ways to run Lighthouse" (replaces the For-agents/For-readers trio)

```
Kicker:    Three ways to run Lighthouse

Card 1 — Hosted, free
  200 searches/day signed-in (30/day anon, per IP). Same corpus,
  same MCP endpoint, no card.
  → Sign in

Card 2 — Hosted, Pro
  1,500 searches/day, top-30 results, watch-on-update, priority
  rerank. $12/mo or $9/seat for teams.
  → Pricing

Card 3 — Self-host (Apache-2.0)
  Run your own Lighthouse with your own corpus, recipes, and
  admin. The retrieval engine is open source; the index is yours.
  → github.com/ElMundiUA/lighthouse
```

### Rewrite — stat cards (4-up)

| Card | Kicker | Value |
|---|---|---|
| 1 | Indexed chunks | `{stats.total_chunks}` |
| 2 | Distinct sources | `{stats.total_sources}` |
| 3 | Role recipes | `{stats.total_recipes}` |
| 4 | **Apache-2.0** | `github.com/ElMundiUA/lighthouse` (champagne link, replaces the MCP-endpoint card) |

Move the MCP endpoint snippet into the existing "Recently indexed" header strip
or into the Three-ways "Hosted, free" card — it's not a stat.

### Rewrite — search-results-mode header strip

When `q` is set, replace the bare hits-count line with:

```
Showing top {n} for "{q}" — Relevance · Newest
{usageRemaining}/{LIMITS[tier]} left today · resets 00:00 UTC
{tier === "anon" && "Sign in for 200/day — no card →"}
```

The "Sign in" inline call is the first soft-conversion. It only renders for
anon; it's a link not a button.

### Rewrite — rate-limited banner

Current copy is fine, but lead with the upgrade not the apology:

```
Daily search limit reached ({usageCount} of {LIMITS[tier]}).
{anon: "Sign in for 200/day — no card. Or upgrade to Pro for 1,500/day."}
{free: "Upgrade to Pro for 1,500/day."}
Counter resets at 00:00 UTC.
```

### Design tweaks

- Drop the "MCP endpoint" stat card; put a `Get the snippet →` button in the
  Three-ways "Hosted, free" card that scrolls to or links to `/home`.
- Replace the For-agents/For-readers/What-is trio with the Three-ways block
  above. Three cards, equal weight, **OSS in the third slot with the GitHub
  star count + license badge** (see Trust block below).
- Hero subhead should mention `Apache-2.0` inline — the OSS signal needs to
  live above the fold.
- Add a one-line ribbon under the search bar: `Powered by 71K chunks · BM25 +
  pgvector + cross-encoder rerank · Last ingest 2 min ago`. Reuses
  `recentRepos` freshness.
- In the recently-indexed table, the "Recipes" column shows up to 3 — bump to
  4 and right-align. The eyeflow currently dies after the first slug.
- Search-results grid: bump `gap-x-10` to `gap-x-6` on `md:` to fit one more
  result per viewport; the cards are too sparse on wide screens.
- Anonymous visitor with `q` and hits: render a `dim` upsell strip *between
  rank 4 and 5* — `Pro shows top-30 and re-ranks the long tail. $12/mo.` —
  not a modal, just a row in the grid styled `border-champagne/30 bg-champagne/[0.03]`.

---

## `src/pages/home.astro` — signed-in dashboard

**Diagnosis.** Copy: the page reads like an internal README — informative but
no merchandising; `tier === "free"` blurb buries "Upgrade to Pro" in a
parenthetical, and the daily-cap line doesn't say what happens when you hit it.
Design: three identical code-block sections (Claude Desktop / Claude Code /
Cursor) eat the fold; the upgrade CTA is one underlined word among 40.

### Rewrite — page heading + subhead

```
Heading:   Welcome, {firstName}.
Subhead:   Your agent is one config block away from the canonical corpus.
           {tier === "free" ? "200 searches/day · Pro is 1,500." :
                              "1,500 searches/day · thanks for backing the project."}
```

### Rewrite — "Your access" card

```
Signed in as {user.email} on the {tier} tier.

{dailyCap.toLocaleString()} searches/day. Hits 80% → warning, 100% → 429 until
00:00 UTC. Same MCP endpoint regardless of tier; rate limit enforces per-user.

{tier === "free" && (
  Pro lifts the cap to 1,500/day, opens top-30 results, and adds watch-on-update
  for the sources you care about. $12/mo, no contract.
  → Upgrade to Pro
)}
```

### Rewrite — "Plug it into your agent"

```
Heading:    Plug it into your agent
Subhead:    Lighthouse speaks MCP (Model Context Protocol). Any MCP-capable
            client picks up two tools: `search` for ranked facts,
            `fetch_source` for the original paragraph. Pick your client.
```

Then the three snippets, but tighten each to a single sentence:

| Client | One-liner |
|---|---|
| Claude Code | One-line install. Add `--scope user` to make it global. |
| Claude Desktop | Drop into the config JSON; restart. Tools appear in the picker. |
| Cursor | `~/.cursor/mcp.json`. Reload Cursor; tool appears in the composer. |

### Rewrite — "What to ask" intro

```
The corpus is canonical SDLC reference, not Stack Overflow. Phrase queries
like you'd ask a senior IC: name the framework, name the concept, name the
RFC if you know the number. The reranker handles fuzzy phrasing.
```

The 6 example queries are good; keep them but link to `/?q=...` (home with
inline results) instead of `/search?q=...` so the funnel returns to the home
hero.

### Rewrite — "More" footer block

Drop `/sections` (it's not in nav anyway). Add OSS:

```
- About the project — what's indexed, how it's built
- Pricing — upgrade or comp a teammate
- GitHub — self-host or send a PR (Apache-2.0)
```

### Design tweaks

- The three client snippets are visually identical (same `<pre>` styling).
  Add a small `<span class="kicker">` row under each header with a logo
  glyph + "Recommended for production" / "Recommended for dev" so the user
  has a reason to pick one. Don't add icons we don't have — text kickers fine.
- Make the upgrade CTA in "Your access" a real `btn-secondary` button, not a
  text link. Anchor it `mt-3 inline-flex`.
- Add a "Recent searches" / "Saved queries" empty-state below the snippets
  — even an empty card with `Sign your agent in once and we'll show recent
  queries here.` plants the seed for the Pro feature without promising it.
  Skip if no backend support exists today.
- The aside ("MCP endpoint") should also expose a one-click copy button —
  this is the single most-copied string on the page.
- Move the `<hr>`s to `border-white/5` instead of the global `border-rule` —
  they currently separate sections too aggressively for a dashboard feel.

---

## `src/pages/pricing.astro` — 4-tier grid

**Diagnosis.** Copy: tiers say what you get but not who they're for; the Pro
"Watch-on-update" and "Priority rerank queue" lines need a one-liner of
clarification or they read as filler. Design: the four cards are equal-weight
even though Pro has the champagne ring — the visual hierarchy is right but the
copy hierarchy isn't; Self-hosted is buried as a footer paragraph when it
should be the **fifth pricing tile** ("Free, your hardware").

### Rewrite — page heading + subhead

```
Heading:   Pick a tier
Subhead:   Same corpus on every tier — IETF RFCs, OWASP, NIST SP-800,
           framework docs, methodology, post-cutoff release streams. Caps
           reset 00:00 UTC. Cancel anytime, prorated. Self-host for free
           if you'd rather run your own index.
```

### Rewrite — tier cards (concise headers)

| Tier | Header copy | Audience line (new, italic small) |
|---|---|---|
| Anonymous | `Free · No account` | _Kicking the tyres._ |
| Reader | `Free · Sign in` | _Daily driver for one engineer._ |
| Pro | `$12/mo · One engineer, serious about it` | _Lifts the cap, opens top-30, watches sources you care about._ |
| Team | `$9/seat/mo · Min 2 seats` | _Shared usage view, GitHub-org SSO (rolling)._ |

### Rewrite — Pro feature clarifications

| Current bullet | Replace with |
|---|---|
| Reranker + summary boost | Cross-encoder rerank + Qwen-summary weighted higher in BM25 |
| Watch-on-update (RSS / releases) | Notify on changes to sources you bookmark — RFCs, release notes, framework docs |
| Priority rerank queue | Your queries jump the rerank queue under load |
| Unlimited MCP endpoints | One token per agent / per machine, revocable from `/home` |

### Rewrite — Self-hosted (promote from footer paragraph to fifth tile)

```
Kicker:    Self-host
Price:     Free · Apache-2.0
Audience:  _Your hardware, your corpus, your admin._
Bullets:
  - Full retrieval engine, MCP server, admin panel — open source
  - Bring your own corpus + role recipes
  - We help you stand it up — by arrangement
  - github.com/ElMundiUA/lighthouse
CTA:       Read the deployment guide →   (link to repo README#deploy)
Footer:    Need quarterly source curation or custom ingest?
           lighthouse@harborgang.com
```

### Design tweaks

- Switch the grid from `md:grid-cols-4` to `md:grid-cols-3 lg:grid-cols-5`
  so Self-host lives in the row. Anon + Reader collapse into a single
  "Free" card with a tab/toggle between "Anon" and "Signed in" — they're
  both $0 and the redundancy reads as padding.
- Add a comparison row under the grid: `Searches/day · Top-K · MCP tokens
  · Watch-on-update · Cross-encoder rerank · SSO · Source code`. Keep it
  to one screen, no scroll. Mark champagne ticks for Pro/Team only where it
  actually differs.
- Annual toggle currently shows `−25%` — confirm the math (12→9 is −25%,
  ✓). Add a `Save $36/yr` literal next to the Pro card when annual is
  active; numeric savings beat percentage.
- The `team-seats` input uses `!important` overrides — replace with a real
  `input-number` component styled with the champagne caret and tabular-nums.
  The current `<input>` looks like it leaked from a debug build.
- "Subscribe" button text is generic. Use `Start Pro` / `Start team plan` —
  verb + tier name, easier to scan.
- When `cfg.token` is missing (Paddle not wired), the fallback is silent —
  add a small `text-mist/45 text-xs` line under the button: `Checkout is
  loading…` so QA doesn't think it's broken.

---

## `src/pages/about.astro` — long-form explainer

**Diagnosis.** Copy: solid voice, accurate, but reads like the README — the
"Built in the open" paragraph buries the headline (Apache-2.0, self-host) at
the bottom, and the Context7 comparison is good but defensive ("our bet
is..."). Design: a single long centred `max-w-2xl` column with five `<h2>`s
in a row gives no scannability — no sidebar, no TOC, no anchors.

### Rewrite — page heading + subhead

```
Heading:   What is Lighthouse?
Subhead:   A grounding layer for coding agents. Open-source retrieval engine,
           hosted index, MCP endpoint. We're a finder, not a republisher.
```

### Rewrite — section order (move OSS up)

1. **Why Lighthouse exists** _(new, opening section)_
2. **Built in the open** _(promoted from #5)_
3. **Why a finder, not a republisher**
4. **How retrieval works**
5. **What's indexed**  _(new, replaces "Subscribing")_
6. **How it differs from Context7**
7. **Subscribing or self-hosting**

### Rewrite — section bodies

**Why Lighthouse exists**
> Coding agents are good at writing code, bad at remembering specs. Ask an
> agent for "the OAuth 2.0 PKCE flow" and you get a confident paraphrase that
> may or may not match RFC 7636. Lighthouse closes that gap — every agent
> query goes through a corpus of canonical SDLC reference, and answers come
> back with a pointer to the source you can actually cite. No training-data
> recall, no hallucinated section numbers.

**Built in the open**
> The retrieval engine is at
> [github.com/ElMundiUA/lighthouse](https://github.com/ElMundiUA/lighthouse)
> under Apache 2.0. Everything we run in production — ingest pipeline,
> chunker, summariser, BM25+pgvector retrieval, cross-encoder reranker, MCP
> server, admin panel — is in that repo. Self-host with your own corpus and
> role recipes; we run the hosted index as a convenience, not a moat.

**Why a finder, not a republisher**
> Keep current copy — it's the strongest paragraph on the page.

**How retrieval works**
> Tighten current copy to one paragraph and add the audit number:
> Every chunk carries a Qwen-7B one-sentence summary, 3–5 topic tags, and
> 6–12 search-relevant keywords. BM25 over a weighted tsvector (summary &
> keywords A, tags B, content C) plus cosine similarity on the chunk
> embedding go through reciprocal-rank fusion; the top candidates feed a
> cross-encoder reranker. **Weekly audit mean is 3.07/5 — just above our
> 3.0 useful-result threshold; we publish the score and the failure modes.**

**What's indexed** _(new)_
> Across 21 role recipes — Developer, DevOps, Security, ML, SRE, Testing,
> Mobile, Architecture, Designer, Reviewer, Planning, Decomposition,
> Clarification, PM, Data Eng, Embedded, Web3, Gamedev, Performance,
> Network, Self-heal — Lighthouse pulls from four buckets:
>
> 1. Canonical standards — RFCs, OWASP, NIST SP-800, MITRE ATT&CK
> 2. Library / framework docs — the Tier-1 mainstream for each role
> 3. Practitioner literature — Brendan Gregg, Cagan, Torres, NN/g, etc.
> 4. Post-cutoff streams — release notes, RFC drafts, framework changelogs

**How it differs from Context7**
> Keep current copy. It's accurate and direct.

**Subscribing or self-hosting**
> Anonymous gets 30/day per IP to evaluate. Signed-in free is 200/day. Pro
> is $12/mo for 1,500/day, top-30 results, and watch-on-update. Or
> [self-host](https://github.com/ElMundiUA/lighthouse) — your hardware, your
> corpus, your admin.

### Design tweaks

- Add a left-side sticky TOC on `md:` — six links, champagne hover. The
  page is long enough to justify it; the TOC also reads as serious
  documentation, not marketing.
- Promote the GitHub link from inline text to a `glass` card with the
  repo's star count badge (proxied via shields.io) and an Apache-2.0
  pill. Place it under the `Built in the open` heading.
- Add an audit-coverage callout block in the "How retrieval works"
  section — `dl` of `Chunks: 71K · Sources: 14K · Summary coverage: 98%
  · Mean usefulness (last 100 queries): 3.07/5` — same visual weight as a
  pull quote.
- Drop `chunk-prose` for the new sections and use the standard
  `text-mist/80 leading-relaxed` so the headings can have champagne
  underlines (matches the rest of the site).
- Add inline anchor links (`#built-in-the-open`) so we can deep-link from
  the home page / footer to the OSS section specifically.

---

## `src/pages/sections.astro` — role-recipe directory

**Diagnosis.** Copy: blurbs are pure framework lists ("Pydantic / FastAPI /
SQLAlchemy"), which is fine but doesn't say *what the role does with
Lighthouse* — and "Section" is internal jargon nobody outside the team uses.
Design: page is orphaned (not in nav), 2-column dense list with no visual
hierarchy, no preview of what each role's top results look like.

### Decision

Rather than fix this page in isolation, **fold it into the home page** as a
21-chip ribbon under the search bar:

```
Browse by role:  Developer · DevOps · Security · ML · SRE · Testing · Mobile ·
                 Architecture · Designer · Reviewer · Planning · Decomposition ·
                 Clarification · PM · Data Eng · Embedded · Web3 · Gamedev ·
                 Performance · Network · Self-heal
```

Each chip = link to `/?q=<role's query>`. This re-uses the existing logic;
no new page needed.

### If we keep the page

Rewrite the heading + subhead:

```
Heading:   Role recipes
Subhead:   21 curated source rosters. Each recipe selects a Tier-1 mainstream
           set, layers in canonical standards, practitioner writing, and
           release streams, then auto-tags chunks for the rerank boost. Click
           a recipe to run its starter query against the live corpus.
```

Rewrite each blurb to two-clause form: `[what you'd use it for] · [Tier-1
sources]`. Example:

| Old | New |
|---|---|
| Developer — Pydantic / FastAPI / SQLAlchemy / TypeScript / React | Developer · API + frontend patterns · Pydantic, FastAPI, SQLAlchemy, TS, React |
| Security — OWASP / RFCs / NIST / MITRE ATT&CK | Security · Vuln classes, auth flows, threat models · OWASP, RFCs, NIST, MITRE |

### Design tweaks

- If the page stays, add a search input at the top scoped to recipes only
  ("Filter recipes...").
- Each card should show a tiny `chunks: N` count next to the title so the
  user knows which recipes are deep vs. starter.
- The `→` glyph in the corner is dead UI. Replace with a champagne
  `hover:underline` on the title and drop the arrow column.
- Add to footer nav (currently only `GitHub · MCP · About · Privacy · Terms
  · Cookies`) — sandwich `Recipes` between MCP and About.

---

## `src/pages/chunk/[id].astro` — single chunk view

**Diagnosis.** Copy: the "Dispatch not in the archive" 404 copy is great and
on-voice; the main view is mostly correct but the byline `{source}:Filed
{date}` is awkward — the source label is a description not a name. Design:
this page is hit primarily as a deep-link from agent transcripts, so it
should be fast and pointer-y; right now it renders the full chunk content,
which we don't want to be the long-term posture (finder, not republisher).

### Rewrite — fallback / not-found copy

Current is fine. Leave it.

### Rewrite — chunk view (de-emphasise full body)

```
Byline:   {sourceLabel.split(":")[0]} · Filed {date}
Title:    {prettyName}
Source:   <a> {sourceUrl} ↗ </a>   (champagne, prominent — this is the CTA)

— change body section header to —

What Lighthouse extracted:
  <Qwen summary · 1 line>

Topic: <tag · tag · tag>
Keywords: <keyword · keyword · …>

— then —

Read it at the source ↗   (champagne button-style link, mt-6)

— and a small dim disclosure —

  <details>
    <summary>Show the indexed paragraph (~{n} words)</summary>
    {chunk body}
  </details>
```

Net: title + summary + source link are above the fold; the chunk body is
collapsed by default. We're a finder, not a republisher — the page should
say so visually.

### Design tweaks

- Drop the `font-display text-5xl` title — too loud for a deep-link landing.
  `text-3xl md:text-4xl` is plenty.
- Replace the bottom "More like this" with a 3-result `/?q={prettyName}`
  preview block — runs server-side, caps top-3, links open the home with
  inline results. Keeps the visitor in the funnel.
- Add an "Add to MCP" floating CTA bottom-right: `Plug your agent in →
  /home`. Anyone landing on a chunk page is here because an agent pointed
  them; they're the highest-intent visitor we get.

---

## Cross-cutting

### OSS positioning

The site currently mentions Apache-2.0 in **one paragraph of `/about`** and
the **GitHub link in the footer**. That's not enough. Three placements:

1. **Hero secondary line** — add `Apache-2.0` to the subhead literal so it's
   visible to the first-paint visitor. Example: `...indexed, ranked, and
   pointed back to the original. Apache-2.0, self-host or use the hosted
   index.`
2. **Home — Three-ways block** (proposed above) — three equal-weight cards;
   "Self-host" is one of them, not a footnote.
3. **Pricing — fifth tile** (proposed above) — `Free · Apache-2.0` next to
   the paid tiers. This is the single biggest trust signal a developer-tool
   pricing page can carry.

Footer reorg (recommended):

```
Build:    GitHub · Docs · MCP endpoint · Roadmap
Read:     About · Recipes · Pricing · Changelog
Legal:    Privacy · Terms · Cookies · Contact
```

Three columns, not the current single-row link salad. Keep the
`© Harbor Gang. Built in the open.` line on a separate row.

### Global SEO / OG

| Tag | Current | Proposed |
|---|---|---|
| `<title>` default | Lighthouse — Knowledge for AI Agents | Lighthouse — grounding layer for coding agents |
| meta description | A knowledge base of canonical SDLC reference for AI agents | Lighthouse indexes 71K chunks of canonical SDLC reference — RFCs, OWASP, NIST, framework docs — and serves them to coding agents over MCP. Apache-2.0. Hosted free or self-host. |
| og:title (home) | same as title | Stop your agent inventing RFC numbers. |
| og:description | same as meta | 71K indexed chunks across 14K canonical sources. Plug your coding agent into the MCP endpoint and get cited answers. Apache-2.0. |

**og:image strategy.** Commission one master OG card: dark champagne-on-ink,
the wordmark `Lighthouse.` centred, a single line of subtype (`grounding
layer for coding agents`), and the corpus numbers as small caps along the
bottom (`71K · 14K · 21 · Apache-2.0`). 1200×630 PNG, deliver to
`/public/og.png`. Page-specific OG cards can come later — one master image
is the 80/20 first move.

Add `<meta name="twitter:image" content="...">`, `<meta property="og:image"
content="...">`, and `<meta property="og:url"
content={Astro.url.toString()}>` to `Site.astro`. Currently missing
twitter:image and og:image entirely.

Per-page title overrides we should set explicitly (not just `title` prop):

| Page | Title |
|---|---|
| `/` (anon) | Lighthouse — grounding layer for coding agents |
| `/about` | About Lighthouse — open-source retrieval for SDLC reference |
| `/pricing` | Lighthouse pricing — $12/mo Pro, free anon, free self-host |
| `/home` | Hook your agent up — Lighthouse |
| `/chunk/[id]` | {prettyName} — Lighthouse |

### Trust block

Add a `Trusted by` strip on the home page (above footer) once we have any of
the following — none today, listed in approximate order of impact for a
developer-tool audience:

1. **GitHub stars badge** (live shields.io) — first thing to add, free, no
   one to ask permission of. Even 50 stars reads as legitimate.
2. **Hacker News post badge** when we launch — `> 100 points` is the
   threshold.
3. **MCP client logos** — Claude, Cursor, Continue, Cline, Zed — these are
   *protocol-supports* logos, not endorsements; safe to ship since we
   genuinely speak MCP.
4. **Testimonial quotes** — one engineer, one PM, one security person; ask
   three Ship-internal users for two-sentence quotes once usage is real.
5. **"Built by Harbor Gang"** — link to harborgang.com when that site has
   anything to show.

Single sentence to add today, even with no logos: `Used in production by the
Ship engineering team and the Harbor Gang internal toolchain.` Honest,
specific, no exaggeration.

### Conversion funnel

Cold visitor walking `/` → signup → upgrade:

1. **`/` → tries a search.** Friction today: anon hits 30/day without a
   single explicit nudge to sign in. _Fix_: after rank-3 of any anon
   search, render a single inline strip `Signed-in readers get 200/day and
   top-15 results. Sign in →`. Non-blocking, in-grid, dismissable.

2. **Search → signup.** Friction today: the "Sign in" header button is
   generic and doesn't say why. _Fix_: make the header CTA dynamic — anon
   reads `Sign in (free, no card)`, signed-in-free reads `Upgrade to Pro`,
   Pro reads avatar/initial. The cap-pill next to it is good; keep.

3. **Signup → upgrade.** Friction today: `/home` mentions Pro once in a
   parenthetical inside a wall of MCP snippets. _Fix_: persistent Pro
   upsell card in the right column when cap usage > 50%, hide otherwise.
   Copy: `You're at {n} of 200 today. Pro is 1,500/day, $12/mo. Upgrade
   →`. Replace generic value-prop bullets with the user's actual usage
   pattern. Conversion math always wins.

Out of scope but worth flagging: there's no `Recently used by you` view of
which sources/queries the current user has hit. That's a Pro-feature shaped
hole — when it ships, it goes on `/home` and becomes the top retention
hook. For now, don't promise it.

---

## Asset / copy quick-wins (apply everywhere)

- Replace `"Knowledge for AI Agents"` (current default title literal) with
  `"grounding layer for coding agents"` — `knowledge` is fuzzy, `grounding
  layer` is the term the audience already uses.
- Wherever we say `RAG`, don't. The audience knows. `grounding`,
  `retrieval`, `cited answers` are the right words.
- Replace every instance of `AI agent` with `coding agent` on marketing
  pages — narrower, more specific, attracts the right user.
- Strip "the lighthouse" (lowercase, definite article) from `/home` copy —
  cute internally, confusing externally. The product name is Lighthouse
  (proper noun, capital L, no article).
- Cap copy says `Counter resets at 00:00 UTC` — fine, but add `(00:00
  UTC = {tz-localised time})` server-side for the visitor's TZ. Reduces
  support questions.
- The `byline` / `section-kicker` / `kicker` classes are used
  interchangeably across pages — pick one and replace-all. Champagne-on-mist
  uppercase tracking is the right look; the inconsistency is the only
  problem.
