# Flat-RAG migration spike

> Status: in progress (2026-05-17). Both engines coexist; nothing is
> being removed yet. The comparison run is the gate that decides
> whether we deprecate Graphiti.

## Goal

Stand up a flat-RAG retrieval engine alongside the current Graphiti
path, run both against the same canonical-queries audit, and
decide on data — not vibes — whether the entity-extraction layer
pays for itself. If it doesn't, deprecate Graphiti for a 70% cost
cut and a 10× faster ingest, with chunk-level time-facts gaining
us a feature we never had (per-version retrieval, post-cutoff
filter).

## Hypothesis

Audit evidence already shows the episode-body second pass is the
dominant retrieval signal — fact triples surface as top-K hits
~30% of the time, prose snippets ~70%. Removing entity extraction
should lose **at most** the fact-triple share, while saving ~70%
of OpenAI spend (entity+edge extraction is the bulk of ingest
cost).

The compensating feature is chunk-level time-facts that Graphiti's
edge-level temporal layer never delivered: `published_at`,
optional `version`, optional `superseded_by`. This lets us answer
"give me only post-cutoff sources" or "give me PyTorch 2.5 docs",
which the current path can't.

## What stays untouched

Nothing below is modified during the spike. These are the rails
the existing production path runs on:

- `src/lighthouse/core/graph.py` (Graphiti `KnowledgeGraph`)
- All `:Episodic` / `:Entity` / `RELATES_TO` data in Neo4j
- The existing `/mcp/` endpoint and its `search`/`fetch_entity`/
  `fetch_source`/`propose` tools
- All running CronJobs (Phase 9b/10/11/role-yamls/coverage-audit)
- `dekus/lighthouse:latest` image — the flat path is additive

## What's new

- `src/lighthouse/core/flat_graph.py` — `FlatGraph` facade,
  `:Chunk` label, fulltext + vector indexes, RRF-fused hybrid
  search with time-aware filters.
- `lighthouse ingest-flat` CLI (TODO) — same `drain()` shape but
  routes to `FlatGraph.upsert_document`.
- Per-source `--backend=flat` flag on the ingest CronJob template
  (TODO) so we can backfill one source-yaml at a time.
- `lighthouse mcp --backend=flat` (TODO) — serves the same
  three tools, but reads `:Chunk` instead of `:Episodic`.
- Separate audit invocation `lighthouse coverage-audit
  --backend=flat` (TODO).

Coexistence guarantee: `:Chunk` label is disjoint from
`:Episodic` and `:Entity`. Neo4j fulltext / vector indexes carry
their own names (`flat_chunk_content`, `flat_chunk_embedding`).
Removing the flat path is `MATCH (c:Chunk) DETACH DELETE c` plus
`DROP INDEX flat_chunk_*` — zero impact on the Graphiti path.

## Comparison protocol

1. **Backfill** the same source set into the flat graph.
   - Start with two yamls that show the strongest signal:
     `phase10-rfcs.yaml` (canonical content, ~60 RFCs) and
     `phase11-post-cutoff.yaml` (recent RSS + GH releases — the
     post-cutoff slice).
   - Hold all other variables constant: same connectors, same
     chunker, same `published_at` extraction.

2. **Audit run.** Re-trigger `lighthouse-coverage-audit` against
   both backends. The canonical queries don't change. Same Haiku
   judge. Same `useful >= 3.0/5` threshold.

3. **Compare on three axes:**
   - **Quality**: mean gap-rate, mean usefulness, per-domain
     breakdown. The flat path must be within −5 pp / +0.1 useful
     of the Graphiti path to win on parity, or strictly better to
     win outright.
   - **Cost**: OpenAI bill per 1000 ingested docs. The flat path
     should be ≤ 30% of Graphiti's per-doc cost (1 embedding call
     vs 2-3 LLM calls + N embeddings).
   - **Latency**: wall-clock seconds per ingested doc. Target ≥
     5× faster (no extraction round-trips).

4. **Time-facts smoke test** (qualitative): run a handful of
   queries with `after=<frontier_cutoff>` and `version=<...>`.
   The flat path should surface only post-cutoff or
   version-matching content; the Graphiti path can't filter that
   way today.

## Decision matrix

| Flat quality vs Graphiti | Decision |
|---|---|
| ≥ Graphiti on mean | Deprecate Graphiti within 2 weeks |
| Within −5 pp on mean | Deprecate Graphiti, accept the regression as the cost of 70% cheaper / 5× faster ingest |
| Worse by 5-15 pp on mean | Add the optional local-model summarisation layer (see below) and re-test |
| Worse by > 15 pp | Keep Graphiti; abandon flat |

The "deprecate" branches involve a one-month sunset where both
backends run, the MCP server defaults to flat, and the Graphiti
endpoint is reachable behind `--backend=graphiti` for rollback.

## Optional layer — local model for cheap summarisation

If the flat path loses 5-15 pp on quality, a per-chunk summary
field would likely close the gap. Not full entity extraction:
just one sentence ("This RFC defines the JWT compact serialisation
format with header.payload.signature segments…") plus 3-5 tags
("JWT", "JOSE", "auth-token", "JWS").

**Candidate model:** Liquid AI LFM2-1.2B or Qwen2.5-3B-Instruct
via Ollama in the same `lighthouse` namespace. CPU-only (cluster
has no GPUs); inference ~2-5 s per chunk on a 1500m CPU pod —
acceptable since this is async batch work.

**Why not gpt-4o-mini for this:** the cost is fine ($0.002/chunk),
but the latency (a network round-trip per call) makes it a poor
fit for batch summarisation. A pod-local model removes the
network round-trip from the loop.

**Why not Graphiti's full extraction:** because we already
established it's overkill for retrieval — and brittle (the
context-window blowups we just spent the session debugging).

Cost ceiling if we keep this layer: ~+$2/mo for inference
electricity / pod CPU. Negligible.

## Rollback story

At any point, even after the decision lands:

```bash
# Stop flat path
kubectl --context do-fra1-ship-prod -n lighthouse delete cronjob \
  -l lighthouse.backend=flat

# Delete flat data
kubectl --context do-fra1-ship-prod -n lighthouse exec neo4j-0 -- \
  cypher-shell -u $NEO4J_USER -p $NEO4J_PASSWORD \
  'MATCH (c:Chunk) DETACH DELETE c'

# Drop indexes
kubectl ... 'DROP INDEX flat_chunk_content; DROP INDEX flat_chunk_embedding; ...'
```

Graphiti corpus untouched. MCP server keeps serving from
`:Episodic` by default.

## Tracking

Linear-style task IDs in this session's TaskCreate stream:

- #102 FlatGraph spike — core + smoke test (in progress)
- #103 Flat ingest CLI + drain
- #104 Flat MCP server + audit A/B
- #105 Local-model fact extraction layer (optional)

Baseline numbers to beat at audit time:

- Graphiti current: **67.1% gap-rate, 2.35 useful** (post Phase
  9b partial-state on 2026-05-17)
- Mean cost per doc Graphiti: **~$0.003-0.005**
- Mean ingest wall-time per RFC: **~20-40 s**
