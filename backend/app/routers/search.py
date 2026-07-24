from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List

from app.deps import get_db
from app.schemas.common import ApiResponse
from app.schemas.search import PathwaySearchRequest
from app.services.search_service import search_entries

router = APIRouter()


@router.get("/search/entries")
async def search_entries_endpoint(
    q: str = Query(..., description="查询词"),
    input_type: Optional[str] = Query(None),
    view_mode: str = Query("table"),
    organism_name: Optional[str] = Query(None),
    source_types: Optional[List[str]] = Query(None),
    review_statuses: Optional[List[str]] = Query(None),
    page: int = Query(1),
    page_size: int = Query(20),
    sort_by: Optional[str] = Query(None),
    sort_order: str = Query("asc"),
    db: AsyncSession = Depends(get_db),
):
    cards, pagination, graph_highlights = await search_entries(
        db,
        q=q,
        input_type=input_type,
        view_mode=view_mode,
        organism_name=organism_name,
        source_types=source_types,
        review_statuses=review_statuses,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )

    data = {
        "items": [c.model_dump(by_alias=True) for c in cards],
        "pagination": pagination.model_dump(by_alias=True),
    }
    if graph_highlights:
        data["graphHighlights"] = graph_highlights

    return ApiResponse(data=data)


@router.post("/search/pathways")
async def search_pathways(
    request: PathwaySearchRequest,
    db: AsyncSession = Depends(get_db),
):
    return ApiResponse(data={"items": [], "graph": None})
