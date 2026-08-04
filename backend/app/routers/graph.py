from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List

from app.deps import get_db
from app.schemas.common import ApiResponse
from app.services.graph_service import build_graph_payload, expand_edge_group

router = APIRouter()


@router.get("/graph")
async def get_graph(
    center_compound_id: Optional[str] = Query(None, description="中心化合物ID"),
    depth: int = Query(2, description="展开层数"),
    limit_nodes: Optional[int] = Query(None, description="最大节点数"),
    selection_mode: Optional[str] = Query(None, description="节点选择模式"),
    source_types: Optional[List[str]] = Query(None, description="来源筛选"),
    review_statuses: Optional[List[str]] = Query(None, description="审核状态筛选"),
    db: AsyncSession = Depends(get_db),
):
    payload = await build_graph_payload(
        db,
        center_compound_id=center_compound_id,
        depth=depth,
        limit_nodes=limit_nodes,
        selection_mode=selection_mode,
        source_types=source_types,
        review_statuses=review_statuses,
    )
    return ApiResponse(data=payload.model_dump(by_alias=True))


@router.get("/graph/edge-groups/{edge_group_id}/edges")
async def expand_edge_group_endpoint(
    edge_group_id: str,
    db: AsyncSession = Depends(get_db),
):
    edges = await expand_edge_group(db, edge_group_id)
    return ApiResponse(data={
        "edgeGroupId": edge_group_id,
        "edges": [e.model_dump(by_alias=True) for e in edges],
    })
