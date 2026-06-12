"""Prometheus metrics.

One registry-default set of counters/histograms the rest of the engine
increments; ``/metrics`` (mounted in api/main.py) exposes them in the
text exposition format. Single-process model — matches how the engine
deploys (one uvicorn per container, scale via replicas). Each replica
is scraped separately; no multiprocess registry gymnastics.

Label-cardinality rules: route templates (not raw paths), workspace ids
(an instance hosts tens of tenants, not millions), coarse outcomes.
"""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)

HTTP_REQUESTS = Counter(
    "lighthouse_http_requests_total",
    "HTTP requests by route template and status code.",
    ["method", "route", "status"],
)

HTTP_LATENCY = Histogram(
    "lighthouse_http_request_seconds",
    "HTTP request latency by route template.",
    ["method", "route"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

SEARCHES = Counter(
    "lighthouse_searches_total",
    "Retrieval searches by workspace and whether they were a gap "
    "(zero hits at response time; the LLM-classified gap lands in "
    "query_log, not here).",
    ["workspace", "gap"],
)

IMPORTER_RUNS = Counter(
    "lighthouse_importer_runs_total",
    "Importer runs by terminal status.",
    ["status"],
)

WEBHOOK_DELIVERIES = Counter(
    "lighthouse_webhook_deliveries_total",
    "Webhook delivery attempts by outcome (delivered / failed / dead).",
    ["outcome"],
)


def render() -> tuple[bytes, str]:
    """The /metrics payload: (body, content_type)."""
    return generate_latest(), CONTENT_TYPE_LATEST
