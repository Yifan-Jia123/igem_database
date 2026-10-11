from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db
from app.models import Compound, Reaction, ReactionCompound
from app.schemas.common import ApiResponse
from app.schemas.structure_search import (
    StructureSearchCompoundHit,
    StructureSearchReactionHit,
    StructureSearchResult,
)

router = APIRouter()


@router.get("/ketcher/search")
async def search_structure_by_inchikey(
    inchikey: str = Query(..., description="Ketcher generated InChIKey"),
    db: AsyncSession = Depends(get_db),
):
    key = inchikey.strip().upper()
    if not key:
        return ApiResponse(success=False, error={"code": "BAD_REQUEST", "message": "InChIKey is required"})

    # Match both columns. inchi_key is the official ChEBI value, inchi_key_derived
    # is RDKit's on-the-fly computation from the stored smiles -- across 710
    # comparable records the two differ in only 2 (CHEBI:231826 / CHEBI:53643,
    # where ChEBI's own smiles and standard_inchi contradict each other), neither
    # can be judged wrong, so both keys are accepted. Accepting only one column
    # would leave a caller who "computed the other key with another tool"
    # unable to find those 2.
    matches_key = or_(Compound.inchi_key == key, Compound.inchi_key_derived == key)

    compound_result = await db.execute(
        select(Compound)
        .where(matches_key)
        .order_by(Compound.compound_id)
    )
    compounds = [
        StructureSearchCompoundHit(
            compound_id=compound.compound_id,
            name=compound.name,
            chebi_id=compound.chebi_id,
            smiles=compound.smiles,
            inchi_key=compound.inchi_key,
            structure_image_url=compound.structure_image_url,
            chebi_url=compound.chebi_url,
            description=compound.description,
        )
        for compound in compound_result.scalars().all()
    ]

    reaction_result = await db.execute(
        select(ReactionCompound, Reaction, Compound)
        .join(Reaction, ReactionCompound.reaction_id == Reaction.reaction_id)
        .join(Compound, ReactionCompound.compound_id == Compound.compound_id)
        .where(matches_key)
        .order_by(Reaction.reaction_id, ReactionCompound.role, Compound.compound_id)
    )
    reactions = [
        StructureSearchReactionHit(
            reaction_id=reaction.reaction_id,
            rhea_id=reaction.rhea_id,
            rhea_url=reaction.rhea_url,
            equation=reaction.equation,
            direction=reaction.direction.value if hasattr(reaction.direction, "value") else str(reaction.direction),
            role=relation.role.value if hasattr(relation.role, "value") else str(relation.role),
            compound_id=compound.compound_id,
            compound_name=compound.name,
            chebi_id=compound.chebi_id,
            source_type=reaction.source_type.value if hasattr(reaction.source_type, "value") else str(reaction.source_type),
            review_status=reaction.review_status.value if hasattr(reaction.review_status, "value") else str(reaction.review_status),
        )
        for relation, reaction, compound in reaction_result.all()
    ]

    result = StructureSearchResult(
        inchikey=key,
        compounds=compounds,
        reactions=reactions,
    )
    return ApiResponse(data=result.model_dump(by_alias=True))
