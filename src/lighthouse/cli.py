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

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lighthouse")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve", help="Run the FastAPI app under uvicorn")

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

    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")

    if args.cmd == "serve":
        return _serve()
    if args.cmd == "mcp":
        return _mcp(args.transport, args.host, args.port)
    if args.cmd == "ingest":
        if args.source == "markdown":
            return asyncio.run(_ingest_markdown(args.path))
        if args.source == "web":
            return asyncio.run(_ingest_web(args.urls))
        if args.source == "github":
            return asyncio.run(
                _ingest_github(args.slug, branch=args.branch, ext=args.ext)
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
    from lighthouse.core.graph import KnowledgeGraph
    from lighthouse.librarian.agent import Librarian
    from lighthouse.mcp.server import run_http, run_stdio
    from lighthouse.proposals.store import GitProposalStore

    settings = get_settings()
    graph = KnowledgeGraph(settings)
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


async def _drain(connector, *, source_prefix: str) -> int:
    """Shared drain loop: pull SourceDocuments from a connector and
    upsert each as an episode. Reused by every ingest subcommand so the
    error/log behavior is consistent across sources.
    """
    from lighthouse.core.graph import KnowledgeGraph

    graph = KnowledgeGraph()
    await graph.initialize()

    n = 0
    try:
        async for doc in connector.ingest():
            await graph.upsert_episode(
                name=doc.title,
                body=doc.body,
                source=f"{source_prefix}:{doc.source_id}",
                reference_time=doc.reference_time,
            )
            n += 1
            logger.info("ingested: %s", doc.title)
        logger.info("done — %d documents ingested from %s", n, source_prefix)
    finally:
        await graph.close()
    return 0


async def _ingest_markdown(path: Path) -> int:
    from lighthouse.connectors.markdown import MarkdownConnector

    return await _drain(MarkdownConnector(path), source_prefix="markdown")


async def _ingest_web(urls: list[str]) -> int:
    from lighthouse.connectors.web import WebConnector

    return await _drain(WebConnector(urls), source_prefix="web")


async def _ingest_github(slug: str, *, branch: str, ext: list[str] | None) -> int:
    from lighthouse.connectors.github import GitHubConnector

    if "/" not in slug:
        raise SystemExit(f"github slug must be OWNER/REPO, got {slug!r}")
    owner, repo = slug.split("/", 1)
    connector = GitHubConnector(
        owner=owner,
        repo=repo,
        branch=branch,
        file_extensions=ext if ext else None,
    )
    return await _drain(connector, source_prefix=f"github:{slug}@{branch}")


if __name__ == "__main__":
    sys.exit(main())
