# Source discovery channels for role recipes

> Curation reference. Where to look for "what should go in this
> role's recipe" — without scraping Context7 or any other
> competitor's curated index. Every entry below is independent
> public data, opensource list, or our own authority registry.

## Tier 1 — mainstream tooling (parity layer)

Cross-reference at least 2 of these signals before adding a Tier 1
source. A library that appears in only one channel is a candidate;
a library that appears in three is a clear pick.

### Opinionated quarterly curators
- **Thoughtworks Technology Radar** — `https://www.thoughtworks.com/radar`
  Adopt/Trial/Assess/Hold ratings, ~100 tools per quarterly issue.
- **CNCF Landscape** — `https://landscape.cncf.io/`
  Categorised map of cloud-native tooling. YAML at
  `github.com/cncf/landscape` is parseable.
- **Martin Fowler's recommended reading + bliki**
  `https://martinfowler.com/bliki/` (also a Tier 3 source — overlap
  is fine).

### Self-reported usage surveys
- **Stack Overflow Developer Survey** — `https://survey.stackoverflow.co/`
  Most-used / most-loved / most-wanted, breakdown by role. CSV
  dataset is openly licensed.
- **JetBrains Developer Ecosystem Survey** — `https://www.jetbrains.com/lp/devecosystem-<year>/`
  Independent alternative to SO; richer on JVM / IDE ecosystem.
- **State of JS / CSS / HTML** — `https://stateofjs.com/`,
  `https://stateofcss.com/`. Annual, role-specific.
- **State of DevOps (DORA)** — `https://dora.dev`
  Authoritative on devops practices.

### Hard-usage data (game-resistant)
- **PyPI download stats** — `https://pypistats.org/api/packages/<pkg>/recent`
- **npm download counts** — `https://api.npmjs.org/downloads/point/last-month/<pkg>`
- **crates.io** — `https://crates.io/api/v1/crates?sort=downloads`
- **rubygems** — `https://rubygems.org/api/v1/gems/<name>.json`
- **pkg.go.dev imported-by counts** — visible in the UI per package

### GitHub-side signal (open API)
- **GitHub search by stars + topic** —
  `https://api.github.com/search/repositories?q=topic:<topic>+stars:>10000`
- **GitHub Topics** — `https://github.com/topics/<topic>`
  (web-framework, orm, observability, service-mesh, iac, etc.)
- **GitHub Trending** —
  `https://github.com/trending/<lang>?since=monthly`

### Community-curated meta-lists (the "awesome-X" ecosystem)
Each is GitHub markdown — directly ingestible via our
`github_tree` connector.

- `github.com/sindresorhus/awesome` — root meta-list
- `github.com/vinta/awesome-python`
- `github.com/avelino/awesome-go`
- `github.com/rust-unofficial/awesome-rust`
- `github.com/sorrycc/awesome-javascript`
- `github.com/sindresorhus/awesome-nodejs`
- `github.com/Kristories/awesome-guidelines`
- `github.com/ramitsurana/awesome-kubernetes`
- `github.com/dastergon/awesome-sre`
- `github.com/davidsonfellipe/awesome-wpo` — performance
- `github.com/Hack-with-Github/Awesome-Hacking` — security
- `github.com/madd86/awesome-system-design`
- `github.com/sjsyrek/awesome-product-management`

### Quality / trust scoring services (independent)
- **Snyk Advisor** — `https://snyk.io/advisor/npm-package/<pkg>`
  Maintenance / security / popularity scores.
- **npm quality score** — visible on `npmjs.com/package/<pkg>`.
- **OpenSSF Scorecard** — `https://securityscorecards.dev/` — best
  for security-critical Tier 1 picks.

### Industry annual reports
- DORA `dora.dev`
- Puppet State of DevOps
- O'Reilly Technology Radar (free preview)
- ThoughtWorks Tech Radar (already listed; the gold standard)

## Tier 2 — canonical standards (predefined URL patterns)

These don't need "discovery". We have authoritative URL templates.

| Domain | Pattern | Examples |
|---|---|---|
| Network/auth/crypto | `https://datatracker.ietf.org/doc/html/rfc<N>` | RFC 8446 TLS, RFC 9000 QUIC, RFC 7519 JWT |
| Web security | `https://cheatsheetseries.owasp.org/cheatsheets/<X>.html`, `https://owasp.org/Top10/`, `https://owasp.org/www-project-application-security-verification-standard/` | OWASP Top 10, ASVS |
| Compliance | `https://nvlpubs.nist.gov/nistpubs/SpecialPublications/NIST.SP.<NNN>.pdf`, `https://csrc.nist.gov/pubs/sp/800/<N>/final` | SP 800-53, 800-63, 800-207 |
| Web standards | `https://www.w3.org/TR/<spec>/`, `https://w3c.github.io/<spec>/` | WCAG, ARIA, fetch spec |
| ECMAScript | `https://tc39.es/ecma262/`, `https://github.com/tc39/proposal-<x>` | language spec + proposals |
| Language refs | `https://docs.python.org/3/reference/`, `https://doc.rust-lang.org/reference/`, `https://go.dev/ref/spec` | per-language |
| Threat / attack | `https://attack.mitre.org/`, `https://cwe.mitre.org/data/definitions/` | MITRE ATT&CK, CWE |
| Academic | `https://arxiv.org/list/cs.<sub>/recent`, `https://www.usenix.org/conferences/all` | papers, conf talks |
| Books online | `https://sre.google/books/` (free SRE book), `https://martinfowler.com/eaaCatalog/` | published refs |

## Tier 3 — methodology / practitioner authors (our authority registry)

Curated list of recognised voices per role. ~3-5 names per role,
maintained in `data/source-research/authority-registry.yaml`
(committed). This is the layer Context7 has by definition zero
overlap with — we're indexing the *literature about engineering*,
not the framework docs themselves.

Selection rule: an author qualifies if they meet at least one:
- Published a recognised book in the domain (DDIA, Refactoring,
  SRE Book, etc.)
- Cited as canonical in two or more state-of-X / handbook references
- Run a long-standing (5+ years) high-quality blog cited by their
  peers

Initial registry seed (full list lives in the yaml):

```
developer:    Martin Fowler, Sandi Metz, Peter Norvig, Robert Martin
architecture: Vaughn Vernon, Eric Evans, Mark Richards
security:     Bruce Schneier, Troy Hunt, Adam Shostack, Daniel Miessler
sre:          Google SRE Book, Honeycomb blog, Brendan Gregg, Charity Majors
pm:           Marty Cagan, Teresa Torres, Jeff Patton, Reforge, Lenny's
planning:     Mike Cohn, Roman Pichler, Scaled Agile Framework
validation:   Kent C. Dodds, Dan North (BDD), Janet Gregory
mobile:       Android Compose blog, React Native engineering, Apple WWDC
performance:  Brendan Gregg, web.dev (Core Web Vitals), k6 blog
designer:     Nielsen Norman Group, Smashing Magazine, shadcn.dev
decomposition: Mike Cohn (Mountain Goat), Jeff Patton, Burrows
```

## Tier 4 — streaming / time-aware (mostly automatable)

### Language / framework official blogs
```
python:    https://blog.python.org/feeds/posts/default
rust:      https://blog.rust-lang.org/feed.xml
go:        https://go.dev/blog/feed.atom
node:      https://nodejs.org/en/feed/blog.xml
typescript: https://devblogs.microsoft.com/typescript/feed/
swift:     https://www.swift.org/atom.xml
kotlin:    https://blog.jetbrains.com/kotlin/feed/
deno:      https://deno.com/blog/feed
bun:       https://bun.com/blog/rss.xml
```

### Framework blogs / changelogs
```
react:     https://react.dev/rss.xml
nextjs:    https://nextjs.org/feed.xml
svelte:    https://svelte.dev/blog/rss.xml
django:    via github_releases django/django
fastapi:   via github_releases fastapi/fastapi
```

### Cloud provider what's-new
```
aws:    https://aws.amazon.com/about-aws/whats-new/recent/feed/
azure:  https://azurecomcdn.azureedge.net/.../updates.rss
gcp:    https://cloud.google.com/feeds/release-notes.xml
do:     https://docs.digitalocean.com/release-notes/index.xml
```

### Security feeds
```
nvd_cve:        https://nvd.nist.gov/feeds/xml/cve/misc/nvd-rss-analyzed.xml
gh_advisories:  https://github.com/advisories.atom
krebs:          https://krebsonsecurity.com/feed/
schneier:       https://www.schneier.com/feed/
hackernews_sec: https://hnrss.org/newest?q=security
```

### Kubernetes / cloud native ecosystem
```
k8s:        https://kubernetes.io/feed.xml
cncf:       https://www.cncf.io/feed/
honeycomb:  https://www.honeycomb.io/blog/feed
grafana:    https://grafana.com/blog/index.xml
```

### Automated derivation
For every Tier 1 entry whose `connector == github_tree`, the
recipe compiler auto-derives a Tier 4 `github_releases` entry
unless explicitly opted out. That covers ~80% of release-note
ingestion without manual curation.

## Workflow (no Context7 anywhere)

```
1. Pick a role (start with highest-gap one — see audit).
2. Run discover script:
     uv run python tools/role_recipe_discover.py security
   Output: pre-filled recipe with T1 (from GitHub Topics + awesome
   list + npm/PyPI downloads), T2 (from URL templates), T3 (from
   authority-registry), T4 (auto-derived).
3. Eyeball T3 in particular — author registry shouldn't surprise
   anyone, but local context might suggest swaps.
4. tools/role_recipe_compose.py → compiled yaml.
5. lighthouse runner --once --backend=flat --config compiled/<role>.yaml
6. lighthouse coverage-audit --backend=flat → measure.
```

Independent, deterministic, no competitor curation in the chain.
