"""Public retrieval endpoints.

These mirror the MCP tools a consuming agent calls: ``search`` for a
hybrid query, ``fetch`` for a specific node by id. Both are stateless
GETs so any HTTP client (curl, Cursor, Claude Desktop, our own Ship
agents) can hit them without ceremony.

Authentication: none. Retrieval is the public face of a Lighthouse
instance. To restrict reads, deploy behind an ingress that enforces
its own auth.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

router = APIRouter(tags=["retrieval"])


class SearchHit(BaseModel):
    """One hit from a hybrid search call.

    ``score`` is implementation-defined (Graphiti returns a fused score
    over vector + BM25 + graph distance) — clients should treat it as
    ordinal, not absolute.
    """

    node_id: str
    summary: str
    score: float
    source_url: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHit]


class FetchResponse(BaseModel):
    node_id: str
    content: str
    provenance: dict[str, str] = Field(default_factory=dict)


@router.get("/search", response_model=SearchResponse)
async def search(
    q: Annotated[str, Query(min_length=1, description="Natural-language query")],
    top_k: Annotated[int, Query(ge=1, le=50)] = 10,
) -> SearchResponse:
    # Stub — graph integration lands in core/graph.py.
    return SearchResponse(query=q, hits=[])


@router.get("/fetch/{node_id}", response_model=FetchResponse)
async def fetch(node_id: str) -> FetchResponse:
    # Stub — looks up a node by id.
    if not node_id:
        raise HTTPException(status_code=400, detail="node_id required")
    return FetchResponse(node_id=node_id, content="", provenance={})
