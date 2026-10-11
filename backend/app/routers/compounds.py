from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db
from app.schemas.common import ApiResponse
from app.services.entity_service import load_compound_card
from app.services.graph_service import suggest_displayable_compounds

router = APIRouter()


@router.get("/compounds/suggest")
async def suggest_compounds_endpoint(
    q: str = Query(..., min_length=1, description="前缀/包含 查询词"),
    limit: int = Query(12, ge=1, le=40),
    db: AsyncSession = Depends(get_db),
):
    """Compound-dictionary autocomplete for the pathway composer.

    Lower-cased matching over every displayable compound (id, bare/dashed ChEBI
    number, name), ranked exact → id/ChEBI prefix → name prefix → name contains.
    Never returns water/proton/diphosphate or unnamed placeholder rows.
    """
    compounds = await suggest_displayable_compounds(db, q=q, limit=limit)
    return ApiResponse(data=[
        {
            "compoundId": c.compound_id,
            "name": c.name,
            "chebiId": c.chebi_id,
        }
        for c in compounds
    ])


@router.get("/compounds/{compound_id}/card")
async def get_compound_card(
    compound_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Assembled in `app.services.entity_service.load_compound_card`, shared with `/bundle`."""
    card = await load_compound_card(db, compound_id)
    if card is None:
        return ApiResponse(success=False, error={"code": "NOT_FOUND", "message": f"Compound {compound_id} not found"})
    return ApiResponse(data=card.model_dump(by_alias=True))
