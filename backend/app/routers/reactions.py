from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.deps import get_db
from app.models import Reaction, ReactionCompound, Compound
from app.schemas.common import ApiResponse
from app.schemas.reaction import ReactionDetail
from app.schemas.compound import CompoundCard
from app.utils.compound_filters import displayable_compound_filters

router = APIRouter()


@router.get("/reactions/{reaction_id}")
async def get_reaction_detail(
    reaction_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Reaction).where(Reaction.reaction_id == reaction_id)
    )
    rxn = result.scalar()
    if not rxn:
        return ApiResponse(success=False, error={"code": "NOT_FOUND", "message": f"Reaction {reaction_id} not found"})

    rc_result = await db.execute(
        select(ReactionCompound, Compound)
        .join(Compound, ReactionCompound.compound_id == Compound.compound_id)
        .where(ReactionCompound.reaction_id == rxn.reaction_id)
        .where(*displayable_compound_filters())
    )
    substrates, products = [], []
    for rc, cpd in rc_result.all():
        card = CompoundCard(
            compound_id=cpd.compound_id,
            name=cpd.name,
            chebi_id=cpd.chebi_id,
            smiles=cpd.smiles,
            average_mass=float(cpd.average_mass) if cpd.average_mass else None,
            chebi_url=cpd.chebi_url,
            structure_image_url=cpd.structure_image_url,
        )
        if rc.role.value == "substrate":
            substrates.append(card)
        else:
            products.append(card)

    detail = ReactionDetail(
        reaction_id=rxn.reaction_id,
        rhea_id=rxn.rhea_id,
        rhea_url=rxn.rhea_url,
        equation=rxn.equation,
        direction=rxn.direction.value,
        ec_number=rxn.ec_number,
        smiles=rxn.smiles,
        atom_map_image_url=rxn.atom_map_image_url,
        substrates=substrates,
        products=products,
        source_type=rxn.source_type.value if rxn.source_type else "swiss_prot",
        review_status=rxn.review_status.value if rxn.review_status else "official",
    )

    return ApiResponse(data=detail.model_dump(by_alias=True))
