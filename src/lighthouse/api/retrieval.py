"""Public retrieval endpoints.

These mirror the MCP tools a consuming agent calls:

- ``GET /search`` — find facts by natural-language query.
- ``GET /fetch_entity/{node_id}`` — resolve one entity by uuid.
- ``GET /fetch_source/{episode_id}`` — pull the original source chunk
  a fact was extracted from. Prefer this over multiple ``fetch_entity``
  calls when a fact's one-line summary isn't enough.

The legacy ``/fetch/{node_id}`` alias is kept for clients that haven't
moved to the renamed endpoint yet — it forwards to ``fetch_entity``
unchanged.

Authentication: none. Retrieval is the public face of a Lighthouse
instance. To restrict reads, deploy behind an ingress that enforces
its own auth.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from lighthouse.api.dependencies import get_graph, get_workspace

router = APIRouter(tags=["retrieval"])


class SearchHit(BaseModel):
    """One chunk returned by ``/search``.

    ``summary`` is the chunk's heading + snippet; ``episode_ids`` carries
    the chunk uuid — feed it to ``/fetch_source`` for the full text.
    ``source_node_id`` / ``target_node_id`` / ``valid_until`` are retained
    for wire compatibility but are always null (flat-RAG has no entity
    layer).
    """

    node_id: str
    summary: str
    source: str | None = None
    """Upstream identifier — a URL or github-tree ref. Lets clients
    link to the original."""
    source_node_id: str | None = None
    target_node_id: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    episode_ids: list[str] = Field(default_factory=list)


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]


class EntityResponse(BaseModel):
    """One entity returned by ``/fetch_entity``."""

    node_id: str
    name: str
    summary: str
    labels: list[str] = Field(default_factory=list)
    attributes: dict[str, str] = Field(default_factory=dict)


class SourceResponse(BaseModel):
    """One source chunk returned by ``/fetch_source``."""

    episode_id: str
    name: str
    source: str
    content: str
    created_at: str | None = None
    valid_at: str | None = None


@router.get("/search", response_model=SearchResponse, include_in_schema=False)
@router.get("/v1/search", response_model=SearchResponse)
async def search(
    q: Annotated[str, Query(min_length=1, description="Natural-language query")],
    graph: Annotated[Any, Depends(get_graph)],
    workspace_id: Annotated[str, Depends(get_workspace)],
    top_k: Annotated[int, Query(ge=1, le=50)] = 10,
) -> SearchResponse:
    hits = await graph.search(q, top_k=top_k, workspace_id=workspace_id)
    # Flat-RAG hits are chunks: no entity layer, so source_node_id /
    # target_node_id / valid_until stay None. valid_from carries the
    # chunk's published_at.
    return SearchResponse(
        query=q,
        hits=[
            SearchHit(
                node_id=h.node_id,
                summary=h.summary,
                source=h.source,
                valid_from=_iso_or_none(h.published_at),
                episode_ids=list(h.episode_ids),
            )
            for h in hits
        ],
    )


def _iso_or_none(v: Any) -> str | None:
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


@router.get("/v1/fetch_entity/{node_id}", response_model=EntityResponse)
@router.get("/fetch_entity/{node_id}", response_model=EntityResponse, include_in_schema=False)
async def fetch_entity(
    node_id: str,
    graph: Annotated[Any, Depends(get_graph)],
) -> EntityResponse:
    if not node_id:
        raise HTTPException(status_code=400, detail="node_id required")
    node = await graph.fetch(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"entity {node_id} not found")
    return EntityResponse(
        node_id=node.node_id,
        name=node.name,
        summary=node.summary,
        labels=list(node.labels),
        attributes={k: str(v) for k, v in node.attributes.items()},
    )


# Legacy alias — keep until consumers migrate.
@router.get("/fetch/{node_id}", response_model=EntityResponse, include_in_schema=False)
async def fetch_legacy(
    node_id: str,
    graph: Annotated[Any, Depends(get_graph)],
) -> EntityResponse:
    return await fetch_entity(node_id, graph)


@router.get("/v1/fetch_source/{episode_id}", response_model=SourceResponse)
@router.get("/fetch_source/{episode_id}", response_model=SourceResponse, include_in_schema=False)
async def fetch_source(
    episode_id: str,
    graph: Annotated[Any, Depends(get_graph)],
    workspace_id: Annotated[str, Depends(get_workspace)],
) -> SourceResponse:
    if not episode_id:
        raise HTTPException(status_code=400, detail="episode_id required")
    src = await graph.fetch_source(episode_id, workspace_id=workspace_id)
    if src is None:
        raise HTTPException(status_code=404, detail=f"source {episode_id} not found")
    return SourceResponse(
        episode_id=src.episode_id,
        name=src.name,
        source=src.source,
        content=src.content,
        created_at=_iso_or_none(getattr(src, "created_at", None)),
        valid_at=_iso_or_none(getattr(src, "valid_at", None)),
    )
