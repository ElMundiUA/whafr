# Home rebalance — MCP setup vs. browser search

## 1. Diagnosis

The page reads MCP-first because **four visual mechanisms compound in the same direction**, not because the founder ranked it first. The hero copy ("Plug your agent into 71K canonical docs") names the MCP job and uses the champagne accent on the chunk count; the search input has no equivalent placement above the H1. Directly under the hero sits the only champagne-bordered panel on the page — the endpoint pill at `border-champagne/30 bg-champagne/[0.04]` — which is the loudest element in the upper region. Five tabs, a code block ~120 px tall, a caption, and a fallback line follow. By the time the eye reaches the search section it has travelled past roughly 720–780 px of MCP material on a 1440×900 viewport and a full screen-height on 1024×768. At 390-wide mobile, the MCP block alone exceeds one phone-screen before search even appears.

The search section is then actively demoted, not merely sequenced. Its kicker reads "**Or** try a search from the browser" — "or" frames it as a consolation. The container narrows from `max-w-3xl` to `max-w-2xl`, the input has no surrounding panel, no accent border, no example queries, no glow. The corpus numbers (71K · 14K · 21) appear under the input at `text-xs text-mist/45` — the smallest, dimmest type on the page — so the proof signal lands attached to the demoted job and is itself whispered.

The deeper structural issue: the numbers are doing **two unrelated jobs simultaneously**. They appear in the H1 ("71K canonical docs") as MCP marketing, and again as a footnote under search as a stat strip. Neither placement treats them as what they actually are — shared product proof that legitimises both jobs. Splitting them across the page means each job carries half the proof, and the search job carries the half that's whispered.

## 2. Three candidate layouts

### A — Side-by-side dual panels

```
+--------------------------------------+
|  KICKER: Grounding for coding agents |
|       H1 one line, centred           |
|     sub: 71K chunks · 14K · 21       |
+--------------------------------------+
| +----------------+ +---------------+ |
| | PLUG AN AGENT  | | SEARCH NOW    | |
| | endpoint URL   | | [_________]   | |
| | [CC][CD][Cu]   | | try: oauth    | |
| | [Cx][gpt]      | | try: k8s probe| |
| | <code block>   | | try: rag chunk| |
| +----------------+ +---------------+ |
|       Recently indexed table...      |
+--------------------------------------+
```

**Rationale.** Two glass panels under a one-line hero, equal width, equal panel chrome (`rounded-2xl border border-white/[0.08] bg-white/[0.025] backdrop-blur-sm p-6 md:p-8`). The hero stops claiming which job matters; the corpus numbers sit in the subheading as shared proof. On desktop both panels fit on the same row at `max-w-[80rem]`; the eye picks whichever matches the visitor's prior. Below `lg` the grid collapses to `grid-cols-1` and search goes on top.

**Lead?** Truly equal on desktop. On tablet/mobile the stack order forces a choice — recommend search on top because the input is zero-friction.

**Tailwind hints.**
- Hero: `pt-12 pb-6 text-center` — keep the radial-gradient glow but widen and centre it across both panels (`h-[500px] w-[1100px] opacity-40`), not under the H1 alone.
- H1: single line, drop the champagne span. Copy candidate: "Grounding for coding agents — and the humans behind them."
- Subheading: `mt-4 font-mono text-sm text-mist/60 flex justify-center gap-x-4`, ` · ` separators.
- Grid wrapper: `mt-10 grid grid-cols-1 lg:grid-cols-2 gap-5`.
- Each panel: identical glass classes. Champagne reserved for (a) input focus ring `focus:ring-2 focus:ring-champagne/40`, (b) active client tab pill. Drop the champagne border on the endpoint pill — demote it to `font-mono text-xs text-mist/65 mt-3 break-all` inside the MCP panel; both panels are now peers.
- Search panel: `<SearchBar>` + three `<a href="/?q=...">` example queries, `text-champagne/80 hover:text-champagne text-sm`.

**Tradeoff.** On 1024×768 with both panels stacked, the page is taller — Recently-indexed sinks further down on mobile. Loses the "everything visible in one glance" quality the current widescreen MCP block has.

### B — Search-as-hero, MCP strip below

```
+--------------------------------------+
|     KICKER: grounding for agents     |
|  H1: Ground your agent. Or just ask. |
|                                      |
|   [  big search input, centred  ]    |
|   try: oauth pkce · k8s probe · rag  |
|   71K chunks · 14K sources · 21 rec  |
+--------------------------------------+
| +----------------------------------+ |
| | MCP endpoint URL | [CC][CD][Cu] | |
| | [Cx][gpt]   <condensed snippet> | |
| +----------------------------------+ |
|       Recently indexed table...      |
+--------------------------------------+
```

**Rationale.** Search input becomes the hero's interactive element — `h-16 text-lg`, centred, `max-w-2xl`, champagne focus ring. Below it: example queries on a single line, then the corpus stats in mono, then the MCP setup as a single wide glass panel where the endpoint URL and the tab strip sit on one row at ≥md, with the active snippet underneath. The MCP block compresses from ~720 px tall to ~340 px by dropping the "Drop this into your client" kicker and tightening padding (`p-5` not `p-8`); per-tab caveats move to `/docs/clients`.

**Lead?** Search leads — zero-friction touch, biggest pixel real-estate at the top. MCP wins on conversion intent but loses on attention because it's compressed.

**Tailwind hints.**
- Hero: `pt-16 pb-4`. H1 `font-display text-5xl md:text-6xl`, no accent span.
- Input wrapper: `relative max-w-2xl mx-auto`; input `w-full h-16 px-6 text-lg rounded-2xl border border-white/10 bg-white/[0.03] focus:border-champagne/60 focus:ring-2 focus:ring-champagne/40`.
- Example chips: `flex flex-wrap justify-center gap-2 mt-4 text-xs text-mist/55`, each `<a href="/?q=...">`, hover-only underline.
- Stats line: `mt-3 font-mono text-sm text-mist/60` centred.
- MCP strip: `mt-12 glass p-6 max-w-3xl mx-auto`. Inside: `flex flex-col md:flex-row md:items-center gap-4` for URL + tabs row, then the active `<CodeBlock>` underneath.

**Tradeoff.** Visitors who arrived specifically to plug Claude Code in must scroll ~600 px before they see a tab. Loses ground for the high-intent agent-installer; gains it for the curious. Search-as-hero also invites empty submits — needs a debounced empty-state hint, or it feels broken.

### C — Two-tab top of page

```
+--------------------------------------+
|     KICKER: grounding for agents     |
|     H1: Two ways into the corpus.    |
|     sub: 71K · 14K · 21              |
|                                      |
|  [ Plug in an agent ][ Just search ] |
+--------------------------------------+
| (pane swaps in place — same height)  |
| +----------------------------------+ |
| |          PANE CONTENTS           | |
| |  either MCP setup or search box  | |
| +----------------------------------+ |
|       Recently indexed table...      |
+--------------------------------------+
```

**Rationale.** A pair of large pill-tabs at the top of a single glass panel; the panel's content swaps between MCP and search at the same height (`min-h-[480px]`), so nothing reflows on switch. Choice persisted via `localStorage` so returning visitors land on their last pick. Corpus numbers live once, in the hero subheading, shared by both panes.

**Lead?** Perfectly balanced — the visitor self-selects. Cost: a self-identification step before any value lands.

**Tailwind hints.**
- Hero: `pt-12 pb-2`, H1 one short line, subheading as in A.
- Tab row: `mt-6 inline-flex rounded-full border border-white/10 bg-white/[0.025] p-1`; each tab `px-6 py-2 rounded-full text-sm`; active reuses the existing `.setup-tab` champagne treatment (`bg-champagne/15 text-champagne font-semibold`).
- Pane wrapper: `mt-8 glass p-6 md:p-8 min-h-[480px]` — fixed min-height prevents jump.
- Vanilla JS extends the existing tab script: read/write `localStorage.lighthouseLandingTab`, default `"search"` (lower-commitment first impression).
- Mobile: both top-tabs stay visible on first paint; pane content scrolls normally.

**Tradeoff.** Self-identification is friction. The "what's MCP?" visitor sees a binary they can't resolve. Also fragile for SEO — only one pane is visible on first paint; off-pane copy is DOM-present but visually hidden, which weakens human-skim scannability.

## 3. Recommendation + the corpus-numbers question

**Ship A (side-by-side).** It's the only layout that asserts equality structurally — same panel chrome, same width, same elevation — rather than asking the visitor to do work (C) or rebalancing by demoting one side (B). The hero stops carrying a job-preference and becomes pure framing.

**Where the 71K / 14K / 21 live.** Hero subheading, directly under the H1, `mt-4 font-mono text-sm text-mist/60` with middot separators — one line, centred, above both panels. They legitimise the panels as shared proof and they're factored out of both jobs so neither panel has to carry marketing weight. Drop the champagne span from the H1 count and remove the duplicate stat line that currently sits under the demoted search input. Single source of truth. The "Recently indexed" table below already does the dynamic-proof job; these are its static complement.

**Biggest risk.** At 1024×768 the two panels become ~384 px wide each after `gap-5`, and the MCP panel's `<CodeBlock>` for Claude Desktop JSON is ~28 chars wide before wrap — it can look claustrophobic next to the breathing-room of the search panel. Visual symmetry breaks even though structural symmetry holds.

**Mitigation.** Two parts. (1) Two-column at `lg:` (≥1024 rendered) only; below `lg` stack vertically with search first. This eats the 1024 cramped-code problem — that viewport gets the stacked version. (2) Inside the MCP panel at desktop widths, make the `<CodeBlock>` horizontally scrollable rather than wrapping (`overflow-x-auto whitespace-pre`), and shorten per-client footnotes to one line with "more →" linking out, so the MCP panel's vertical rhythm matches the search panel's (input + 3 example queries + the already-promoted-up numbers).
