# Role-recipe scheme — competing with Context7 on libraries, winning on what they don't have

> Status: design (2026-05-17). Goal: a deterministic process for
> choosing what to ingest per role, so coverage maps to roles
> (not just to "domains") and our differentiator is structural,
> not vibes.

## Strategic frame

Context7 ships ~680 library docs (mostly auto-curated from doc sites +
GitHub). Their moat is **breadth + manual trustScore + freshness on
mainstream framework docs**. We can't out-breadth them on framework
docs without spending 10× their crawl cost.

But Context7 has zero meaningful coverage of:
- **Canonical standards** — RFC, OWASP, NIST, ISO, W3C
- **Methodology / practice** — patterns, Agile/PRD/PM frameworks,
  SRE / blameless postmortem playbooks
- **Streaming content** — RSS, GitHub release notes, CVE feeds with
  per-chunk `published_at`
- **Time-aware retrieval** — "give me only post-cutoff" or
  "version=N" filters at query time

So the play is **4-tier coverage per role**: enough Tier 1 to be
within striking distance of Context7 on the framework-doc surface
they own, then stack Tiers 2-4 on top as the differentiator.

## Schema: four tiers, one recipe per role

Each role gets one `data/source-research/role-recipes/<role>.yaml`
laid out in four tiers. The role-recipe compiler walks every recipe,
emits a single deduped source list per role, and produces a
coverage-vs-Context7 matrix as a side effect.

```yaml
role: developer
audit_domains: [developer, validation, performance]
queries_sample:
  - "FastAPI dependency injection session"
  - "Pydantic v2 field_validator before vs after"
  - "asyncio TaskGroup exception handling"
  - "SQLAlchemy 2.0 async select"

tier1_mainstream:           # parity with Context7
  - id: pydantic-docs
    connector: github_tree
    args: {slug: pydantic/pydantic, branch: main,
           file_extensions: [.md], include_paths: [docs/]}
    context7_id: /pydantic/pydantic
    rationale: "Top-3 Python typing/validation lib."
    parity: critical

tier2_canonical:            # our differentiator — standards
  - id: peps-index
    connector: web
    args: {urls: ["https://peps.python.org"]}
    rationale: "Python language standards. Not in Context7."

tier3_methodology:          # our differentiator — practice
  - id: fowler-bliki
    connector: sitemap
    args: {root: "https://martinfowler.com",
           include_paths: [/bliki/, /eaaCatalog/]}
    rationale: "Refactoring + DDD patterns. Practitioner-level
                content Context7 doesn't index."

tier4_streams:              # our differentiator — time
  - id: python-blog
    connector: rss
    args: {feeds: ["https://blog.python.org/feeds/posts/default"],
           max_entries: 50}
    rationale: "Post-cutoff Python core changes."
  - id: gh-fastapi-releases
    connector: github_releases
    args: {slug: fastapi/fastapi, max_releases: 30}
    rationale: "Versioned changelog stream."
```

## Tier definitions

### Tier 1 — Mainstream tooling (Context7 parity)

For each role, the 5-15 most-used tools/frameworks. Selection
criteria (any two of three to qualify):

- GitHub stars > 5K
- Listed in latest Stack Overflow Developer Survey "most used"
- Context7 `trustScore >= 7` and `totalSnippets >= 5000`

**Goal**: a developer/devops/etc. asking about their daily tool gets
the same answer they'd get from Context7. Parity, not advantage.

Connector preference: `github_tree` over `sitemap` (faster, more
deterministic) when the project publishes markdown alongside code.

### Tier 2 — Canonical standards (differentiator: depth)

The "back of the book" references. Sources that show up zero times
in Context7's enumerable result set:

- IETF RFCs — networking, auth, crypto
- OWASP — security cheatsheets, top-10
- NIST SP-800 series — compliance refs
- W3C — WCAG, web standards
- ISO/IEC — for compliance-leaning roles
- ACM/IEEE proceedings — for architecture/algorithms

**Goal**: answers on these topics are *better* than Context7's
because Context7 doesn't have them at all.

Connector preference: `web` for static HTML standards, `github_tree`
for living standards that publish in markdown.

### Tier 3 — Methodology / practice (differentiator: practitioner content)

Recognized authors writing on patterns, frameworks, and craft:

- **Developer**: Martin Fowler, Eric Evans, Vaughn Vernon,
  refactoring.guru
- **DevOps**: Google SRE Book, Honeycomb blog, Brendan Gregg
- **Security**: Adam Shostack, Bruce Schneier, OWASP Cheat Sheets
- **PM**: Marty Cagan, Teresa Torres, Jeff Patton, Lenny's Newsletter
- **Self-heal**: PagerDuty postmortems, blameless culture refs
- **Decomposition**: Mountain Goat, Roman Pichler, JPattonAssociates
- **Validation**: Kent C. Dodds, Gergely Orosz on testing
- **Reviewer**: Google Eng Practices, ConventionalComments
- **Mobile**: React Native engineering blog, Vasil Dimov on Compose

**Goal**: practitioner-level "how should I think about X" content.
Context7 lists framework docs; we list the literature on how to
*use* them well.

Connector preference: `sitemap` for blog sites, `rss` if author
maintains a feed.

### Tier 4 — Streaming / time-aware (differentiator: cadence)

Daily/weekly cadence sources where the value is recency:

- Per-language official blog feeds (Python/Rust/Go/Node)
- Cloud provider news (AWS What's New, Azure updates, GCP news)
- GitHub release notes for every Tier 1 entry
- CVE feeds for security/devops
- Status pages / incident reports for SRE

**Goal**: "what's new since my model's training cutoff" answers.
Context7's static snapshots can't deliver this — they aren't
ingesting changelogs as first-class content.

Connector preference: `rss` for blog-style, `github_releases` for
versioned tools.

## Per-role tier allocations

Rough target — number of sources per tier per role. Total budget
~30-50 sources per role:

| Role | T1 (parity) | T2 (canonical) | T3 (practice) | T4 (streams) |
|---|---:|---:|---:|---:|
| developer | 12-15 | 3-5 (PEPs, ECMA) | 3-5 (Fowler, Hickey, Norvig) | 4-6 |
| devops | 10-12 | 5-7 (RFCs, NIST, k8s spec) | 4-6 (SRE Book, Honeycomb) | 6-8 (release notes, status) |
| security | 5-7 | 12-15 (RFCs, OWASP, NIST) | 4-5 (Schneier, Shostack) | 4-6 (CVE, GH advisories) |
| ml | 8-10 | 3-4 (papers, MCP spec) | 4-5 (Karpathy, HF blog) | 5-6 (model releases) |
| validation | 7-9 | 1-2 (Gherkin spec) | 5-7 (Kent Dodds, BDD lit) | 3-4 |
| reviewer | 4-5 | 2-3 (CWE, ConvComments) | 6-8 (style guides) | 2-3 |
| self-heal | 4-5 | 2-3 (RFC 5424 syslog etc) | 8-10 (SRE, postmortems) | 4-6 (RSS) |
| product-manager | 4-6 | 0-1 | 12-15 (Cagan, Torres, JTBD) | 3-4 |
| planning | 3-4 | 2-3 (Scrum Guide, SAFe) | 12-14 (Pichler, JPatton) | 1-2 |
| clarification | 3-4 | 1-2 (Gherkin, INVEST) | 12-15 (BA practice) | 1-2 |
| designer | 6-8 | 4-5 (WCAG, ARIA) | 6-8 (NN/g, design blogs) | 2-3 |
| decomposition | 2-3 | 1-2 (WBS PMI) | 14-16 (Agile lit) | 0-1 |
| architecture | 4-6 | 4-6 (CAP/PACELC papers) | 10-12 (Vernon, Evans, Tilkov) | 1-2 |
| mobile | 8-10 | 0-1 | 4-6 (Android engineering) | 5-7 |
| network | 2-3 | 15-20 (RFCs heavy) | 2-3 | 2-3 |
| performance | 4-6 | 2-3 (Web Vitals spec) | 6-8 (Brendan Gregg, web.dev) | 3-4 |
| ml | 8-10 | 3-4 | 4-5 | 5-6 |

Sum: ~470-650 sources total across all roles. Roughly **2× more than
Context7's "trustScore=10 verified" subset** but distributed by
*relevance to a role*, not by *aggregate library popularity*.

## Compilation pipeline

```
data/source-research/role-recipes/
  developer.yaml        ← human-authored
  devops.yaml
  ...
  security.yaml

tools/role_recipe_compose.py
  - reads every recipe
  - emits data/source-research/compiled/<role>.yaml
    (the actual yaml runner consumes; merges all 4 tiers)
  - emits docs/coverage-vs-context7.md
    (matrix: per-role T1 list × Context7 trustScore / snippets;
     flags Tier 1 sources where Context7 has more snippets so
     we can decide to add a deeper crawl path)
  - emits docs/coverage-differentiators.md
    (T2+T3+T4 list per role — our outright unique surface)
```

The compiler is idempotent. Recipe diffs translate cleanly to
compiled-yaml diffs. Compiled yamls are what the ingest CronJobs
consume. No tier metadata leaks into the runtime path.

## Audit-side feedback loop

Coverage-audit gap-rate per role is the metric this whole scheme
optimises. After a recipe lands:

1. Re-ingest into flat with the new compiled yaml.
2. Run coverage-audit → per-domain gap-rate.
3. If a Tier 1 source has high gap-rate, the source is wrong
   (broken crawl, wrong path filter). Fix the recipe.
4. If gap-rate is low on Tier 2-4 queries that Context7 can't
   answer at all — confirm our differentiator empirically.

Concretely: weekly Sunday audit shows per-role delta. The PR that
adds new role-recipe sources lists "expected drop on these audit
queries". After merge, next audit either confirms or surfaces a
recipe bug.

## Bootstrapping order

We don't need to fill all 17 roles' recipes at once. Order them by
ROI = (current audit gap-rate × role usage frequency):

1. **developer** (100% gap, queried daily) — write recipe, ingest, audit
2. **devops** (83% gap, high-frequency)
3. **security** (60% gap but high-leverage)
4. **self-heal** (frequently asked)
5. **ml** (100% gap, growing usage)
6. … (rest at quarterly cadence)

Each role recipe is ~1-2 hour of human work to curate + 1h of ingest
+ audit. So full set ~30-50 hours of human curation, spread over a
few weeks. Cost in OpenAI / ingest: ~$5-15 per role (flat-RAG
prices), so $100-300 one-off + ~$5/month per role to keep streams
fresh.

## Worked example — developer.yaml

(See `data/source-research/role-recipes/developer.yaml`.)
