from fastapi import APIRouter, Depends, Query, Body
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from typing import Optional, List

from app.deps import get_db
from app.models import Enzyme, Gene, EnzymeReactionEdge, Reaction
from app.schemas.common import ApiResponse, Pagination
from app.schemas.search import EntrySearchRequest, PathwaySearchRequest
from app.schemas.enzyme import EnzymeCard
from app.schemas.pathway import PathwayCard
from app.schemas.graph import GraphPayload

router = APIRouter()


@router.get("/search/entries")
async def search_entries(
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
    """Entry search — by enzyme ID, UniProt ID, Rhea ID, enzyme name, gene name, etc."""
    # Stub: query by primary_name like
    stmt = select(Enzyme).where(Enzyme.primary_name.contains(q)).limit(page_size).offset((page - 1) * page_size)
    result = await db.execute(stmt)
    enzymes = result.scalars().all()

    cards = []
    for enz in enzymes:
        edge_result = await db.execute(
            select(EnzymeReactionEdge).where(EnzymeReactionEdge.enzyme_id == enz.enzyme_id).limit(1)
        )
        edge = edge_result.scalar()
        react = None
        if edge:
            react_result = await db.execute(
                select(Reaction).where(Reaction.reaction_id == edge.reaction_id)
            )
            react = react_result.scalar()

        cards.append(EnzymeCard(
            edge_id=edge.edge_id if edge else "",
            enzyme_id=enz.enzyme_id,
            primary_name=enz.primary_name,
            uniprot_id=enz.uniprot_id,
            database_code=enz.enzyme_id,
            organism_name=enz.organism_name,
            ec_number=react.ec_number if react else None,
            reaction_id=edge.reaction_id if edge else "",
            reaction_equation=react.equation if react else "",
            reaction_direction=react.direction.value if react else "unknown",
            source_type=enz.source_type.value,
            review_status=enz.review_status.value,
        ))

    return ApiResponse(
        data={
            "items": [c.model_dump(by_alias=True) for c in cards],
            "pagination": Pagination(page=page, page_size=page_size, total=len(cards), total_pages=1).model_dump(by_alias=True),
        }
    )


@router.post("/search/pathways")
async def search_pathways(
    request: PathwaySearchRequest,
    db: AsyncSession = Depends(get_db),
):
    """Pathway search — supported as stub; full IDDFS to follow."""
    return ApiResponse(data={"items": [], "graph": None})
