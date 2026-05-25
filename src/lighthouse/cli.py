"""Lighthouse CLI.

Two entry points so far:

- ``lighthouse serve`` — boot the FastAPI app under uvicorn
- ``lighthouse ingest markdown <dir>`` — drain a markdown directory into
  the graph via the markdown connector

The CLI is deliberately thin — argparse, no Click/Typer — so the
opensource side has zero ceremony deps. If we grow more commands the
plan is Typer, not a hand-rolled dispatcher.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lighthouse")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve", help="Run the FastAPI app under uvicorn")

    runner_cmd = sub.add_parser(
        "runner",
        help="Run the scheduled source-runner (drains configured sources on a schedule)",
    )
    runner_cmd.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to sources.yaml; defaults to LIGHTHOUSE_RUNNER_CONFIG",
    )
    runner_cmd.add_argument(
        "--once",
        action="store_true",
        help="Drain every configured source once and exit (ignores schedule)",
    )
    runner_cmd.add_argument(
        "--heartbeat",
        type=float,
        default=30.0,
        help="Seconds between schedule checks (default: 30)",
    )
    runner_cmd.add_argument(
        "--max-concurrent",
        type=int,
        default=5,
        help="Max parallel source ingests. Sitemap connector is polite "
        "by default (per-source rate_limit_per_sec=1.0).",
    )

    mcp_cmd = sub.add_parser("mcp", help="Run the MCP server (for AI clients)")
    mcp_cmd.add_argument(
        "--transport",
        choices=["stdio", "http", "sse"],
        default="stdio",
        help="stdio for desktop clients (Claude Desktop, Cursor); "
        "http/sse for remote agents",
    )
    mcp_cmd.add_argument("--host", default="127.0.0.1")
    mcp_cmd.add_argument("--port", type=int, default=8765)

    ingest = sub.add_parser("ingest", help="Drain a source into the graph")
    ingest_sub = ingest.add_subparsers(dest="source", required=True)

    md = ingest_sub.add_parser("markdown", help="Ingest a directory of .md files")
    md.add_argument(
        "path",
        type=Path,
        help="Directory to scan recursively for .md/.markdown files",
    )

    web = ingest_sub.add_parser("web", help="Ingest one or more web pages")
    web.add_argument("urls", nargs="+", help="URLs to fetch")

    gh = ingest_sub.add_parser("github", help="Ingest doc files from a GitHub repo")
    gh.add_argument("slug", help="OWNER/REPO (e.g. fastapi/fastapi)")
    gh.add_argument("--branch", default="main")
    gh.add_argument(
        "--ext",
        nargs="+",
        default=None,
        help="File extensions to include (default: .md .rst .mdx .txt)",
    )

    audit = sub.add_parser(
        "coverage-audit",
        help="Run canonical-query gap-rate audit and emit JSON metrics",
    )
    audit.add_argument(
        "--queries",
        type=Path,
        default=Path("data/coverage-eval/queries.yaml"),
        help="YAML of {domain: [query, ...]} canonical probes",
    )
    audit.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="top_k per search (default 5)",
    )
    audit.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write JSON results to this path (default: stdout)",
    )
    audit.add_argument(
        "--summary",
        type=Path,
        default=None,
        help="Optional markdown summary alongside JSON",
    )
    # Boost + reranker default to on — matches production search
    # behaviour. ``--no-*`` flags are for measuring the lift of each
    # layer (boost-only, no-boost) during tuning.
    audit.add_argument(
        "--no-summary-boost",
        dest="use_summary_boost",
        action="store_false",
        default=True,
        help="Disable the tsv_boosted column for this run. Default is "
        "ON; pass this to measure the boost lift.",
    )
    audit.add_argument(
        "--no-reranker",
        dest="use_reranker",
        action="store_false",
        default=True,
        help="Disable the post-hybrid gpt-4o-mini reranker. Default is "
        "ON; pass this to measure rerank lift.",
    )

    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")

    if args.cmd == "serve":
        return _serve()
    if args.cmd == "mcp":
        return _mcp(args.transport, args.host, args.port)
    if args.cmd == "runner":
        return asyncio.run(
            _runner(
                args.config,
                once=args.once,
                heartbeat=args.heartbeat,
                max_concurrent=args.max_concurrent,
            )
        )
    if args.cmd == "ingest":
        if args.source == "markdown":
            return asyncio.run(_ingest_markdown(args.path))
        if args.source == "web":
            return asyncio.run(_ingest_web(args.urls))
        if args.source == "github":
            return asyncio.run(
                _ingest_github(args.slug, branch=args.branch, ext=args.ext)
            )
    if args.cmd == "coverage-audit":
        from lighthouse.runner.coverage_audit import run_audit

        return asyncio.run(
            run_audit(
                queries_path=args.queries,
                top_k=args.top_k,
                out_path=args.out,
                summary_path=args.summary,
                use_summary_boost=args.use_summary_boost,
                use_reranker=args.use_reranker,
            )
        )

    parser.error(f"unknown command: {args.cmd}")
    return 2


def _serve() -> int:
    import uvicorn

    uvicorn.run("lighthouse.api.main:app", host="0.0.0.0", port=8000, reload=False)
    return 0


def _mcp(transport: str, host: str, port: int) -> int:
    from pathlib import Path

    from lighthouse.core.config import get_settings
    from lighthouse.core.flat_graph import FlatGraph
    from lighthouse.librarian.agent import Librarian
    from lighthouse.mcp.server import run_http, run_stdio
    from lighthouse.proposals.store import GitProposalStore

    settings = get_settings()
    graph: Any = FlatGraph(settings)
    store = GitProposalStore(Path(settings.lighthouse_proposals_dir))
    librarian = Librarian(settings)

    if transport == "stdio":
        run_stdio(graph, store=store, librarian=librarian)
    else:
        run_http(
            graph,
            store=store,
            librarian=librarian,
            host=host,
            port=port,
            transport="sse" if transport == "sse" else "streamable-http",
        )
    return 0


async def _runner(
    config_path: Path | None,
    *,
    once: bool,
    heartbeat: float,
    max_concurrent: int,
) -> int:
    from lighthouse.core.config import get_settings
    from lighthouse.core.flat_graph import FlatGraph
    from lighthouse.runner import SourceScheduler, StateStore, load_config

    settings = get_settings()
    cfg_path = config_path or Path(settings.lighthouse_runner_config)
    config = load_config(cfg_path)
    if not config.sources:
        logger.warning("no sources configured in %s — nothing to do", cfg_path)
        return 0

    graph: Any = FlatGraph(settings)
    state = StateStore(Path(settings.lighthouse_runner_state))
    scheduler = SourceScheduler(
        config,
        state,
        graph,
        heartbeat_seconds=heartbeat,
        max_concurrent=max_concurrent,
    )
    try:
        if once:
            results = await scheduler.run_once()
            for name, n in results.items():
                logger.info("source %s: %d documents", name, n)
        else:
            await scheduler.run()
    finally:
        await graph.close()
    return 0


async def _ingest_markdown(path: Path) -> int:
    from lighthouse.connectors.markdown import MarkdownConnector
    from lighthouse.ingest import drain

    await drain(
        MarkdownConnector(path), source_prefix="markdown", workspace_id="public"
    )
    return 0


async def _ingest_web(urls: list[str]) -> int:
    from lighthouse.connectors.web import WebConnector
    from lighthouse.ingest import drain

    await drain(WebConnector(urls), source_prefix="web", workspace_id="public")
    return 0


async def _ingest_github(slug: str, *, branch: str, ext: list[str] | None) -> int:
    from lighthouse.connectors.github import GitHubConnector
    from lighthouse.ingest import drain

    if "/" not in slug:
        raise SystemExit(f"github slug must be OWNER/REPO, got {slug!r}")
    owner, repo = slug.split("/", 1)
    connector = GitHubConnector(
        owner=owner,
        repo=repo,
        branch=branch,
        file_extensions=ext if ext else None,
    )
    await drain(
        connector,
        source_prefix=f"github:{slug}@{branch}",
        workspace_id="public",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
