from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.deps import get_db
from app.models import Compound
from app.schemas.common import ApiResponse
from app.schemas.compound import CompoundCard
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
    result = await db.execute(
        select(Compound).where(Compound.compound_id == compound_id)
    )
    cpd = result.scalar()
    if not cpd:
        return ApiResponse(success=False, error={"code": "NOT_FOUND", "message": f"Compound {compound_id} not found"})

    card = CompoundCard(
        compound_id=cpd.compound_id,
        name=cpd.name,
        chebi_id=cpd.chebi_id,
        smiles=cpd.smiles,
        formula=cpd.formula,
        charge=float(cpd.charge) if cpd.charge else None,
        average_mass=float(cpd.average_mass) if cpd.average_mass else None,
        inchi=cpd.inchi,
        inchi_key=cpd.inchi_key,
        structure_image_url=cpd.structure_image_url,
        chebi_url=cpd.chebi_url,
        description=cpd.description,
    )
    return ApiResponse(data=card.model_dump(by_alias=True))
