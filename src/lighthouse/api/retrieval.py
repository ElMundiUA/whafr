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

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from lighthouse.api.dependencies import get_graph
from lighthouse.core.graph import KnowledgeGraph

router = APIRouter(tags=["retrieval"])


class SearchHit(BaseModel):
    """One fact returned by ``/search``.

    A fact is a graph edge — a one-line statement relating two entities
    (``source_node_id`` and ``target_node_id``) extracted from a source
    chunk (``episode_ids``). Clients that want more context either
    drill into an entity via ``/fetch_entity`` or — usually preferred —
    pull the original paragraph via ``/fetch_source``.
    """

    node_id: str
    summary: str
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


@router.get("/search", response_model=SearchResponse)
async def search(
    q: Annotated[str, Query(min_length=1, description="Natural-language query")],
    graph: Annotated[KnowledgeGraph, Depends(get_graph)],
    top_k: Annotated[int, Query(ge=1, le=50)] = 10,
) -> SearchResponse:
    hits = await graph.search(q, top_k=top_k)
    return SearchResponse(
        query=q,
        hits=[
            SearchHit(
                node_id=h.node_id,
                summary=h.summary,
                source_node_id=h.source_node_uuid or None,
                target_node_id=h.target_node_uuid or None,
                valid_from=h.valid_from.isoformat() if h.valid_from else None,
                valid_until=h.valid_until.isoformat() if h.valid_until else None,
                episode_ids=list(h.episode_ids),
            )
            for h in hits
        ],
    )


@router.get("/fetch_entity/{node_id}", response_model=EntityResponse)
async def fetch_entity(
    node_id: str,
    graph: Annotated[KnowledgeGraph, Depends(get_graph)],
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
    graph: Annotated[KnowledgeGraph, Depends(get_graph)],
) -> EntityResponse:
    return await fetch_entity(node_id, graph)


@router.get("/fetch_source/{episode_id}", response_model=SourceResponse)
async def fetch_source(
    episode_id: str,
    graph: Annotated[KnowledgeGraph, Depends(get_graph)],
) -> SourceResponse:
    if not episode_id:
        raise HTTPException(status_code=400, detail="episode_id required")
    src = await graph.fetch_source(episode_id)
    if src is None:
        raise HTTPException(status_code=404, detail=f"source {episode_id} not found")
    return SourceResponse(
        episode_id=src.episode_id,
        name=src.name,
        source=src.source,
        content=src.content,
        created_at=src.created_at.isoformat() if src.created_at else None,
        valid_at=src.valid_at.isoformat() if src.valid_at else None,
    )
