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

    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")

    if args.cmd == "serve":
        return _serve()
    if args.cmd == "mcp":
        return _mcp(args.transport, args.host, args.port)
    if args.cmd == "ingest" and args.source == "markdown":
        return asyncio.run(_ingest_markdown(args.path))

    parser.error(f"unknown command: {args.cmd}")
    return 2


def _serve() -> int:
    import uvicorn

    uvicorn.run("lighthouse.api.main:app", host="0.0.0.0", port=8000, reload=False)
    return 0


def _mcp(transport: str, host: str, port: int) -> int:
    from lighthouse.mcp.server import run_http, run_stdio

    if transport == "stdio":
        run_stdio()
    else:
        run_http(host=host, port=port, transport="sse" if transport == "sse" else "streamable-http")
    return 0


async def _ingest_markdown(path: Path) -> int:
    from lighthouse.connectors.markdown import MarkdownConnector
    from lighthouse.core.graph import KnowledgeGraph

    graph = KnowledgeGraph()
    await graph.initialize()

    connector = MarkdownConnector(path)
    n = 0
    async for doc in connector.ingest():
        await graph.upsert_episode(
            name=doc.title,
            body=doc.body,
            source=f"markdown:{doc.source_id}",
            reference_time=doc.reference_time,
        )
        n += 1
        logger.info("ingested: %s", doc.title)

    logger.info("done — %d documents ingested from %s", n, path)
    await graph.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
