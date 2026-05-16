"""Canonical-query gap-rate auditor.

Replaces the manual audit-wave process. Loads a curated YAML of
``{domain: [query, ...]}``, runs each query through the graph search,
and asks Claude to score each result's *usefulness* (1-5). A query
whose top-5 average falls below the "useful" threshold counts toward
the domain's gap-rate.

This is a metric, not a benchmark — its purpose is operational
("which domains still need new sources?"), not model evaluation.
Output is a single JSON blob + optional markdown summary; intended to
be run weekly from a CronJob so we have a time series of gap-rates.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# Threshold below which a query result is considered "not useful enough
# to ground an answer". 3.0/5 is the audit convention used in the
# series of manual audit waves (avg of 5 hits scored 1-5).
USEFUL_THRESHOLD = 3.0


@dataclass
class QueryResult:
    domain: str
    query: str
    n_hits: int
    avg_useful: float  # mean of per-hit scores (1-5)
    per_hit_scores: list[int]
    coverage: str  # "ok" / "thin" / "empty" (from MCP coverage hint)
    is_gap: bool


@dataclass
class DomainSummary:
    domain: str
    n_queries: int
    gap_rate: float  # 0..1
    mean_useful: float
    queries: list[QueryResult] = field(default_factory=list)


@dataclass
class AuditReport:
    run_id: str
    timestamp: str
    mean_gap_rate: float
    mean_useful: float
    per_domain: list[DomainSummary]
    config: dict[str, Any]


async def run_audit(
    *,
    queries_path: Path,
    top_k: int,
    out_path: Path | None,
    summary_path: Path | None,
) -> int:
    if not queries_path.exists():
        logger.error("queries file not found: %s", queries_path)
        return 1
    raw = yaml.safe_load(queries_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict) or not raw:
        logger.error("queries file must be a non-empty mapping {domain: [...]}: %s", queries_path)
        return 1

    from lighthouse.core.graph import KnowledgeGraph

    graph = KnowledgeGraph()

    # Concurrency cap — Claude judge call is the bottleneck; ~5 in
    # flight keeps p95 latency reasonable without rate-limiting.
    sem = asyncio.Semaphore(5)
    tasks = []
    for domain, queries in raw.items():
        if not queries:
            continue
        for q in queries:
            tasks.append(_audit_one(graph, sem, domain, str(q), top_k))
    results: list[QueryResult] = await asyncio.gather(*tasks)

    per_domain: dict[str, list[QueryResult]] = {}
    for r in results:
        per_domain.setdefault(r.domain, []).append(r)

    summaries = []
    overall_gaps = 0
    overall_useful = 0.0
    overall_n = 0
    for domain, qrs in per_domain.items():
        n = len(qrs)
        gaps = sum(1 for q in qrs if q.is_gap)
        mean_useful = sum(q.avg_useful for q in qrs) / n if n else 0
        summaries.append(
            DomainSummary(
                domain=domain,
                n_queries=n,
                gap_rate=gaps / n if n else 0,
                mean_useful=mean_useful,
                queries=qrs,
            )
        )
        overall_gaps += gaps
        overall_useful += sum(q.avg_useful for q in qrs)
        overall_n += n

    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    report = AuditReport(
        run_id=run_id,
        timestamp=datetime.now(UTC).isoformat(),
        mean_gap_rate=overall_gaps / overall_n if overall_n else 0,
        mean_useful=overall_useful / overall_n if overall_n else 0,
        per_domain=sorted(summaries, key=lambda d: d.gap_rate, reverse=True),
        config={"top_k": top_k, "queries_path": str(queries_path), "useful_threshold": USEFUL_THRESHOLD},
    )

    out_json = json.dumps(asdict(report), indent=2, default=str)
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(out_json, encoding="utf-8")
        logger.info("wrote audit JSON to %s", out_path)
    else:
        print(out_json)

    if summary_path:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(_render_markdown(report), encoding="utf-8")
        logger.info("wrote markdown summary to %s", summary_path)

    return 0


async def _audit_one(
    graph, sem: asyncio.Semaphore, domain: str, query: str, top_k: int
) -> QueryResult:
    async with sem:
        try:
            hits = await graph.search(query, top_k=top_k)
        except Exception:
            logger.exception("search failed for %r — counting as gap", query)
            return QueryResult(
                domain=domain,
                query=query,
                n_hits=0,
                avg_useful=0.0,
                per_hit_scores=[],
                coverage="empty",
                is_gap=True,
            )

        coverage = "ok"
        if not hits:
            coverage = "empty"
        elif len(hits) < max(2, int(top_k * 0.4)):
            coverage = "thin"

        # Score each hit's usefulness relative to the query. Defer to
        # Claude — it's cheap on Haiku and the score we'd compute from
        # cosine similarity alone would be brittle on this corpus.
        scores: list[int] = []
        if hits:
            scores = await _score_hits(query, [h.summary for h in hits])
        avg = sum(scores) / len(scores) if scores else 0.0
        return QueryResult(
            domain=domain,
            query=query,
            n_hits=len(hits),
            avg_useful=avg,
            per_hit_scores=scores,
            coverage=coverage,
            is_gap=avg < USEFUL_THRESHOLD,
        )


async def _score_hits(query: str, summaries: list[str]) -> list[int]:
    """Ask Claude Haiku to rate each hit 1..5 for usefulness vs query.

    Returns one int per summary, in the same order. On API failure
    returns all zeros (counted as gap) rather than aborting the audit.
    """
    from anthropic import AsyncAnthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("ANTHROPIC_API_KEY missing — scoring everything as 0")
        return [0] * len(summaries)
    client = AsyncAnthropic(api_key=api_key)

    bullets = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(summaries))
    prompt = (
        f"Query: {query!r}\n\n"
        f"Candidate facts retrieved from a knowledge base:\n{bullets}\n\n"
        "Rate EACH candidate 1-5 on whether it would help an engineer "
        "answer the query:\n"
        "  1 = irrelevant or wrong topic\n"
        "  3 = on-topic but generic\n"
        "  5 = directly answers / canonical reference\n\n"
        f"Reply with EXACTLY {len(summaries)} integers separated by spaces. "
        "No prose. Example: '4 2 5 1 3'."
    )
    try:
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=80,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            b.text for b in resp.content if getattr(b, "type", "") == "text"
        ).strip()
    except Exception:
        logger.exception("scoring %r failed — treating as zero", query)
        return [0] * len(summaries)

    out: list[int] = []
    for tok in text.replace(",", " ").split():
        try:
            n = int(tok)
            out.append(max(1, min(n, 5)))
        except ValueError:
            continue
    # Pad / truncate so caller can rely on length alignment.
    if len(out) < len(summaries):
        out.extend([0] * (len(summaries) - len(out)))
    return out[: len(summaries)]


def _render_markdown(report: AuditReport) -> str:
    lines: list[str] = []
    lines.append(f"# Coverage audit — {report.run_id}")
    lines.append("")
    lines.append(f"- Timestamp: {report.timestamp}")
    lines.append(f"- Mean gap-rate: **{report.mean_gap_rate * 100:.1f}%**")
    lines.append(f"- Mean usefulness: {report.mean_useful:.2f} / 5")
    lines.append(f"- Useful threshold: {USEFUL_THRESHOLD} / 5")
    lines.append("")
    lines.append("| Domain | Queries | Gap-rate | Mean useful |")
    lines.append("|---|---:|---:|---:|")
    for d in report.per_domain:
        lines.append(
            f"| {d.domain} | {d.n_queries} | "
            f"{d.gap_rate * 100:.0f}% | {d.mean_useful:.2f} |"
        )
    return "\n".join(lines) + "\n"
