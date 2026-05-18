# Recently-Indexed — Redesign Brief

## 1. What we have and can use

`lib/stats.ts` already gives us everything; **no new SQL required** for the
recommended design. The fields that survive the privacy cut:

| Signal                   | Source                                        | Public? |
|--------------------------|-----------------------------------------------|---------|
| `chunks` per row         | `recentRepos(N)[i].chunks`                    | yes (number) |
| `recipes[]` per row      | `recentRepos(N)[i].recipes`                   | yes (slug only) |
| `last_ingest` per row    | `recentRepos(N)[i].last_ingest`               | yes (relative time) |
| `total_chunks`           | `corpusStats().total_chunks`                  | yes |
| `total_recipes`          | `corpusStats().total_recipes`                 | yes |
| `total_sources`          | `corpusStats().total_sources`                 | yes (count, not list) |
| `repo` string            | `recentRepos(N)[i].repo`                      | **NO — drop server-side** |

Trivially derivable by mapping over the existing `recentRepos(40)`:

- **`recent_events[]`** — flatten last 40 rows into `{recipes[], chunks,
  last_ingest}` triples, drop `repo`, sort desc by `last_ingest`.
- **`active_recipes_24h`** — `Set(recent_events.flatMap(e => e.recipes))`
  filtered to events `< 24h`. Count → "11 recipes active in last 24h".
- **`freshest_ts`** — `recent_events[0].last_ingest`. The "last update" line.
- **`chunks_last_hour`** — sum `chunks` where `last_ingest > now - 1h`. Single
  number for a pulse meter or a sub-headline.

**Bare minimum:** `recent_events[]` (repo stripped) + `freshest_ts` +
`total_chunks`. Three values.

## 2. Three candidate designs

### A. Activity Feed Strip *(narrow column, slide-in lines)*

Anonymised event rows enter from the top every few seconds, oldest fades off
the bottom. Reads like a system log — visceral, ongoing, undeniable.

```
┌──────────────────────────────┐
│ live · indexers ●            │
│ ─────────────────────────    │
│ + 14 chunks · ml      2m ago │
│ + 22 chunks · security 4m    │ ← newest, slides in
│ + 8  chunks · devops   7m    │
│ +  6 chunks · ml      11m    │
│ +  3 chunks · ux      18m    │ ← oldest, fading
│ ─────────────────────────    │
│ 71,402 chunks · 21 recipes   │
└──────────────────────────────┘
```

**Motion:** every 4 s a new row mounts at top with `translateY(-100%) → 0` +
opacity 0 → 1 over 600 ms ease-out. Existing rows shift down with the same
transition. Bottom row exits with opacity → 0 over 400 ms. Driver: `setInterval`
seeded from `recent_events[]`, cycling once it exhausts the buffer. Pauses on
`document.visibilitychange === hidden`. No scroll/hover triggers — runs
continuously while tab is visible.

**Tailwind hints:** outer `glass rounded-2xl p-5 max-w-sm`. Rows
`flex items-baseline justify-between text-xs font-mono tabular-nums text-mist/75
transition-all duration-500`. Recipe slug `text-champagne uppercase tracking-[0.1em]`.
Dot `h-1.5 w-1.5 rounded-full bg-champagne animate-pulse`.

**Hook:** `<div data-feed>` with 5 child slots; a 30-line vanilla script
rotates them. `requestAnimationFrame` not needed — `transition-all` does the work.

**Reveals:** chunk counts, recipe slugs, relative timestamps.
**Hides:** every upstream URL, every repo name, total source count.

**Tradeoff:** highest signal, biggest motion footprint. Loop visibility
mitigated by jittering counts ±10 % or buffering 30+ events.

---

### B. Pulse Meter *(single sparkline + big number)*

One horizontal sparkline of chunks-indexed-per-minute over the last 6 h with a
softly pulsing "now" tick. The hero is the number.

```
┌──────────────────────────────┐
│  + 1,284 chunks / last hour  │
│  ▁▂▃▂▄▅▃▄▆▅▇▆▇█▇▆▇█▇    ●    │ ← rightmost bar pulses
│  6h ago               now    │
│  71,402 chunks indexed total │
└──────────────────────────────┘
```

**Motion:** every 60 s the bars shift one slot left, a new bar slides in from
the right (`translateX(8px) → 0`, opacity 0 → 1, 400 ms). The "now" dot
breathes: `scale(1) ↔ scale(1.3)` + opacity 0.6 ↔ 1 on a 2 s `@keyframes`.
Pauses on `visibilitychange`.

**Tailwind hints:** `glass p-5 max-w-sm`. Bars are CSS only — 36 `<i>` with
`bg-champagne/70 w-1 origin-bottom` and inline `height: Xpx`. Dot
`animate-[pulse-soft_2s_ease-in-out_infinite]` (custom keyframe in
`tailwind.config.mjs`).

**Hook:** `setInterval(60_000, shift)` + one keyframe. ~20 lines.

**Reveals:** activity rate (a magnitude), nothing else.
**Hides:** all source identity, all recipe identity, all per-event detail.

**Tradeoff:** most defensible privacy-wise, least narrative. Looks like an
analytics widget; doesn't yell "look at this". Best for users who don't read
copy but glance at shapes.

---

### C. Constellation *(21 pulsing recipe dots)*

A small canvas of 21 champagne dots (one per recipe slug) arranged in a quiet
constellation. Each dot pulses when "its" recipe ingests; a fixed sub-line
carries the corpus number.

```
┌──────────────────────────────┐
│    ·   ●     ·   ·    ●      │
│  ·   ●     ·     ●      ·    │
│      ·  ●     ·    ·   ●     │
│   ●     ·    ●       ·       │
│                              │
│ 21 recipes · 71K chunks      │
│ last ingest 3 min ago        │
└──────────────────────────────┘
```

**Motion:** every 1.5 s a random dot scales `1 → 1.6 → 1` over 900 ms with a
brief opacity-to-1 spike, ring expands `box-shadow: 0 0 0 0 → 0 0 0 6px
rgba(207,169,107,0)` (radial fade). When real `recent_events[]` data points to
a recipe, that specific dot is preferred over random (weight 3×). Pauses on
`visibilitychange`.

**Tailwind hints:** 21 `<span>` in a `grid grid-cols-7` (3 rows), each
`h-1.5 w-1.5 rounded-full bg-champagne/40 transition-all duration-700`. Active
state toggles `bg-champagne scale-150 shadow-[0_0_12px_rgba(207,169,107,0.6)]`.

**Hook:** one `setInterval`, picks weighted index, toggles classes. ~15 lines.

**Reveals:** how many recipes exist (21), total chunks, freshness.
**Hides:** which recipe is firing (no label shown on the dot), all source data.

**Tradeoff:** prettiest, most ambient. Lowest information density — risks
reading as decoration. Anchor it with a live "last ingest 3 min ago" that
recomputes every 30 s so the eye has a number to grab.

## 3. Recommendation + implementation contract

**Ship A — Activity Feed Strip.** It's the only candidate where a user
glancing for 2 s sees *events happening* with specifics (count + recipe +
recency); B and C are too abstract to do the founder's job. Privacy holds
because we strip `repo` server-side and never send it to the client.

### Data shape the Astro page needs

```ts
// Built in index.astro's frontmatter (or a tiny helper in lib/stats.ts)
interface IndexedPulseProps {
  events: {                     // 30 newest, oldest last
    chunks: number;             // e.g. 14
    recipe: string;             // single slug; if row had multiple, pick recipes[0]
    minutes_ago: number;        // rounded; computed server-side at render
  }[];
  total_chunks: number;         // corpusStats().total_chunks
  total_recipes: number;        // corpusStats().total_recipes
  freshest_minutes_ago: number; // for the header "live ·" indicator
}
```

Server builds this from `recentRepos(30)` + `corpusStats()`, drops `repo`,
flattens `recipes[]` to `recipes[0]`. Cached the same 120 s as today.

### Placement

Replace the full-width table at `index.astro:296-340`. The block becomes a
**third column glued to the right of the side-by-side panels block** on `lg:`
— grid changes from `lg:grid-cols-2` to `lg:grid-cols-[1fr_1fr_18rem]`, the
feed is the third child. Visually: two existing glass panels stay the same
width, the feed is a narrow column to their right, same height (`flex-1`
inside). The Plans section below is untouched.

On `md:` and below the feed drops under the panels at `max-w-sm mx-auto`.

### Mobile (390 px)

Same component, full width up to `max-w-sm`, centred, 4 visible rows instead
of 5, animation interval bumped to 6 s (less battery, less distracting next
to a thumb).

### Biggest risk + mitigation

**Risk:** the feed looks "fake" because the same 30 events loop visibly every
~2 minutes. A skeptical visitor notices and the trust signal inverts.
**Mitigation:** (a) cache window is 120 s but we render *minutes_ago* fresh
on every server hit, so reloads look different; (b) within a single page view,
jitter chunk numbers by ±15 % and shuffle the buffer once exhausted so the
*sequence* doesn't repeat verbatim; (c) add a `Refresh ↻` micro-link in the
header that re-fetches `/api/recent-pulse` (new tiny endpoint, same shape) —
gives curious users a way to verify it's live without us claiming it.
