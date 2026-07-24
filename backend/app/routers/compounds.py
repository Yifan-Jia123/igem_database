from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.deps import get_db
from app.models import Compound
from app.schemas.common import ApiResponse
from app.schemas.compound import CompoundCard

router = APIRouter()


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
        structure_image_url=cpd.structure_image_url,
        chebi_url=cpd.chebi_url,
        description=cpd.description,
    )
    return ApiResponse(data=card.model_dump(by_alias=True))
