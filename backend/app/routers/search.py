from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List

from app.deps import get_db
from app.schemas.common import ApiResponse
from app.schemas.search import PathwaySearchRequest, TableByIdsRequest
from app.services.search_service import search_entries, search_enzyme_table, search_enzyme_table_by_ids
from app.services.pathway_service import search_pathways as do_pathway_search, PathwayInputError

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


@router.get("/search/table")
async def search_table_endpoint(
    q: str = Query(..., description="查询词"),
    input_type: Optional[str] = Query(None),
    limit: int = Query(500, le=2000),
    db: AsyncSession = Depends(get_db),
):
    """Aggregated enzyme rows for the table-form search results page."""
    items, total = await search_enzyme_table(db, q=q, input_type=input_type, limit=limit)
    return ApiResponse(data={
        "items": [c.model_dump(by_alias=True) for c in items],
        "total": total,
    })


@router.post("/search/table/by-ids")
async def search_table_by_ids_endpoint(
    request: TableByIdsRequest,
    db: AsyncSession = Depends(get_db),
):
    """Aggregated enzyme rows for an explicit enzyme-id list (order preserved).

    Lets BLAST hits render through the same table-form result surface as
    keyword search, so the data-source / organism / EC filter sidebar operates
    on identical rich rows. ``total`` mirrors the number of input ids.
    """
    items, total = await search_enzyme_table_by_ids(db, enzyme_ids=request.enzyme_ids)
    return ApiResponse(data={
        "items": [c.model_dump(by_alias=True) for c in items],
        "total": total,
    })


@router.post("/search/pathways")
async def search_pathways_endpoint(
    request: PathwaySearchRequest,
    db: AsyncSession = Depends(get_db),
):
    """Pathway-mode search: ordered start → (…via…) → end compound chains.

    Returns the top distinct pathways (each a unique compound chain) with a
    ``graph`` that is the union of every returned pathway's elements (nodes,
    single edges and composite edge groups), so the client can paint the whole
    result set at once and highlight one pathway at a time.
    """
    try:
        cards, payload, total = await do_pathway_search(
            db,
            start_compound_id=request.start_compound_id,
            end_compound_id=request.end_compound_id,
            via_compound_ids=request.via_compound_ids,
            max_steps=request.max_steps,
            source_types=request.source_types,
            review_statuses=request.review_statuses,
            limit=request.limit,
        )
    except PathwayInputError as exc:
        return ApiResponse(
            success=False,
            error={"code": exc.code, "message": exc.message, "details": exc.details},
        )

    return ApiResponse(data={
        "items": [c.model_dump(by_alias=True) for c in cards],
        "total": total,
        "graph": payload.model_dump(by_alias=True),
    })
