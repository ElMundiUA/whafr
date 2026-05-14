"""Public retrieval endpoints.

These mirror the MCP tools a consuming agent calls: ``search`` for a
hybrid query, ``fetch`` for a specific node by uuid. Both are stateless
GETs so any HTTP client (curl, Cursor, Claude Desktop, our own Ship
agents) can hit them without ceremony.

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
    """One hit from a hybrid search call.

    Reflects a Graphiti ``EntityEdge`` — a fact relating two entities,
    with temporal windows attached. Clients that want the full subgraph
    follow ``source_node_id`` / ``target_node_id`` through ``/fetch``.
    """

    node_id: str
    summary: str
    source_node_id: str | None = None
    target_node_id: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]


class FetchResponse(BaseModel):
    node_id: str
    name: str
    summary: str
    labels: list[str] = Field(default_factory=list)
    attributes: dict[str, str] = Field(default_factory=dict)


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
            )
            for h in hits
        ],
    )


@router.get("/fetch/{node_id}", response_model=FetchResponse)
async def fetch(
    node_id: str,
    graph: Annotated[KnowledgeGraph, Depends(get_graph)],
) -> FetchResponse:
    if not node_id:
        raise HTTPException(status_code=400, detail="node_id required")
    node = await graph.fetch(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"node {node_id} not found")
    return FetchResponse(
        node_id=node.node_id,
        name=node.name,
        summary=node.summary,
        labels=list(node.labels),
        attributes={k: str(v) for k, v in node.attributes.items()},
    )
