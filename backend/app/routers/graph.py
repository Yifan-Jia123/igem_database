from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List

from app.deps import get_db
from app.schemas.common import ApiResponse
from app.schemas.graph import GraphPayload
from app.schemas.enzyme import EnzymeCard

router = APIRouter()


@router.get("/graph")
async def get_graph(
    center_compound_id: Optional[str] = Query(None),
    depth: int = Query(2),
    limit_nodes: Optional[int] = Query(None),
    source_types: Optional[List[str]] = Query(None),
    review_statuses: Optional[List[str]] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Return home page or local network graph."""
    payload = GraphPayload()
    return ApiResponse(data=payload.model_dump(by_alias=True))


@router.get("/graph/edge-groups/{edge_group_id}/edges")
async def expand_edge_group(
    edge_group_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Expand overlapping edges in an edge group."""
    return ApiResponse(data={"edgeGroupId": edge_group_id, "edges": []})
