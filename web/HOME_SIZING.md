# Lighthouse home — sizing pass

Sizing, weight, spacing, hierarchy only. No copy changes.

## 1. The size + spacing scale

One H1 per page. Sub-section heads (H2) sit a full step below the hero,
so the eye knows where the billboard ends and the explanatory content
begins. Kickers are conditional — they exist to *introduce* a section
the H2 alone doesn't name, not to decorate every block.

| Role | Tailwind classes | Notes |
| --- | --- | --- |
| **H1** (hero, once) | `font-display text-4xl md:text-5xl font-bold tracking-tight leading-[1.1] text-mist` | Demote from `text-5xl md:text-6xl font-extrabold leading-[1.05]`. Drop the inline `<br/>` — let it wrap naturally inside `max-w-3xl mx-auto`. `font-bold` not `font-extrabold` so the champagne span still has somewhere to push. |
| **H2** (sub-section) | `font-display text-xl md:text-2xl font-semibold tracking-tight text-mist` | Two steps below H1. `font-semibold`, not `font-extrabold`. Remove the `mb-2` after H2 → use `mb-1.5` so the subhead sits closer. |
| **Section kicker** | unchanged class `.section-kicker` (0.7rem uppercase champagne) | Keep only where the H2 doesn't carry the section name. Drop on MCP, Pricing, Engine (their H2s already name them). Keep on Hero (frames the product) and Search (no H2 below it — the kicker *is* the label). |
| **Subhead / lede** under H2 | `text-mist/65 text-sm md:text-base leading-relaxed max-w-xl mx-auto` | Was `text-mist/65` with default size — pin it explicitly so all three subheads match. `max-w-xl` keeps them at ~50ch. |
| **Hero subhead** | `text-mist/65 text-sm md:text-base max-w-xl mx-auto` | Drop `text-base` floor; let it scale with the H1 demotion. `mt-3` not `mt-4`. |
| **Body paragraph** (in-card, in-engine) | `text-mist/70 text-sm leading-relaxed` | Already what pricing/engine cards use. No change. |
| **Code/mono inline** | `font-mono text-xs` (inline `<code>`) / `font-mono text-sm` (endpoint pill, code blocks) | No change. |
| **Card price** | `font-display text-2xl font-bold` | Demote from `text-3xl font-extrabold` so it isn't competing with the section H2. |
| **Section gap (canonical)** | `mt-16` between distinct blocks on the home page | Replaces the current `mt-12 / mt-20 / mt-24 / mt-24` mix. Apply on Search, MCP, Pricing, Engine. |
| **Tight gap** | `mt-8` | For pulse → search (intentional close coupling: "here's the firehose, now query it"). |
| **Hero → pulse** | `mt-6` (and `pb-2` on hero stays) | Already tight; don't widen. |
| **Final bottom** | `mb-16` on Engine section | Replaces `mb-12`; balances the new `mt-16` rhythm. |

Rationale on weight: `font-extrabold` everywhere flattens hierarchy. H1
keeps `font-bold` (still heavy at 48–60px), H2 drops to `font-semibold`
(still confident at 24–32px), and the champagne accent span inside the
H1 stays the loudest single element on the page.

## 2. Per-section edits

### Hero (lines 120–130)

- H1 class: `text-5xl md:text-6xl font-extrabold tracking-tight text-mist leading-[1.05]` → `text-4xl md:text-5xl font-bold tracking-tight text-mist leading-[1.1]`.
- Drop the `<br/>` between `canonical docs` and `your agent` — let the `max-w-3xl` container wrap it.
- Kicker `"Grounding for coding agents"`: **keep**. `mb-4` → `mb-3`.
- Subhead `mt-4 text-mist/65 text-base max-w-xl mx-auto` → `mt-3 text-mist/65 text-sm md:text-base max-w-xl mx-auto`.
- Section wrapper `pt-12 pb-2` → `pt-10 pb-2` (small lift; the H1 demotion already buys vertical room).

### Pulse (IndexedPulse component, line 133)

- No change. Sits visually right under the hero and that coupling is intentional.
- If the component has its own top margin internally, leave it.

### Search (lines 136–150)

- Section wrapper: `mt-12 max-w-2xl mx-auto text-center` → `mt-8 max-w-2xl mx-auto text-center` (tighten pulse→search).
- Kicker `"Search the corpus"`: **keep** — there's no H2 here, the kicker is the section label. No change to class.
- SearchBar: no change.
- Example chips `<ul class="mt-4 ...">`: no change.

### MCP setup (lines 153–235)

- Section wrapper: `mt-20 max-w-3xl mx-auto` → `mt-16 max-w-3xl mx-auto`.
- Drop the kicker `<p class="section-kicker mb-3">Plug your agent in</p>` — the H2 says the same thing.
- Header wrapper `mb-6` → `mb-5`.
- H2 class: `font-display text-3xl md:text-4xl font-extrabold tracking-tight text-mist mb-2` → `font-display text-xl md:text-2xl font-semibold tracking-tight text-mist mb-1.5`.
- Subhead `<p class="text-mist/65 max-w-xl mx-auto">`: → `<p class="text-mist/65 text-sm md:text-base max-w-xl mx-auto leading-relaxed">`.
- Endpoint pill `mb-6` → `mb-5`. Internal classes unchanged.
- Tabs row `mb-4`: no change.
- Tab buttons (`rounded-full px-4 py-1.5 text-sm`): no change.
- Tab-pane caption paragraphs (`text-xs text-mist/55 mt-2`): no change.
- Footer note `mt-4 text-xs text-mist/45 text-center`: no change.

### Pricing (lines 305–334)

- Section wrapper: `mt-24` → `mt-16`.
- Drop the kicker `<p class="section-kicker mb-3">Pricing</p>` — H2 names it.
- Header wrapper `mb-6` → `mb-5`.
- H2 class: `font-display text-3xl md:text-4xl font-extrabold tracking-tight text-mist mb-2` → `font-display text-xl md:text-2xl font-semibold tracking-tight text-mist mb-1.5`.
- Subhead `<p class="text-mist/65">` → `<p class="text-mist/65 text-sm md:text-base max-w-xl mx-auto leading-relaxed">`.
- Card grid `gap-5`: no change.
- Inside both cards — kickers `<p class="section-kicker mb-2">Free</p>` and `Pro`: **keep**. They're the only label on the card and double as the eyebrow above the price; that's a legitimate use of the kicker.
- Price `font-display text-3xl font-extrabold mb-1` → `font-display text-2xl font-bold mb-1`. Apply to both Free and Pro cards.
- Price suffix `<span class="text-sm font-normal text-mist/55"> /mo</span>`: no change.
- Card body paragraphs (`text-sm`, `text-mist/70 text-sm leading-relaxed`): no change.
- CTA links (`text-champagne text-sm font-semibold`): no change.

### Engine (lines 337–365)

- Section wrapper: `mt-24 mb-12` → `mt-16 mb-16`.
- Glass panel `p-8 md:p-10`: no change.
- Grid `gap-6 md:gap-10`: no change.
- Drop the kicker `<p class="section-kicker mb-3">Open-source engine</p>` — the H2 names the section.
- Header `mb-3` after kicker is moot once kicker is gone; H2 `mb-3` stays.
- H2 class: `font-display text-2xl md:text-3xl font-bold mb-3` → `font-display text-xl md:text-2xl font-semibold tracking-tight mb-2`. (This section is *already* a step smaller than MCP/Pricing today — the new scale unifies all three at the same H2 size, which is what we want.)
- Body paragraph `text-mist/70 leading-relaxed mb-4`: → `text-mist/70 text-sm md:text-base leading-relaxed mb-4` (explicit size, matches other subheads/body).
- Mono footer `text-sm font-mono text-mist/55`: → `text-xs font-mono text-mist/55` (it's metadata, not a subhead — drop a step so it stops competing with the body paragraph above it).
- CTA buttons `btn-primary !text-sm` / `btn-secondary !text-sm`: no change.

## 3. Net effect

- One billboard (H1, ~48–60px, bold).
- Three sub-section H2s at one consistent smaller size (~20–24px, semibold), all visually peers of each other.
- Kickers survive in three places: hero (frames the product), search (no H2), and inside the pricing cards (the eyebrow on each card). Removed from MCP / Pricing-section / Engine where the H2 carries the load.
- One canonical vertical rhythm: `mt-16` between sections, with `mt-8` for the intentional pulse→search pairing and `mt-6` hero→pulse.
- One subhead style across hero / MCP / pricing — same colour, same size, same width — so the eye learns the pattern once.
