from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.deps import get_db
from app.models import Enzyme, Reaction, Compound, SourceType, ReviewStatus, Direction
from app.schemas.common import ApiResponse

router = APIRouter()


@router.get("/metadata/filter-options")
async def get_filter_options(
    module: str = Query(None, description="当前模块"),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(func.distinct(Enzyme.organism_name)).where(Enzyme.organism_name.isnot(None)))
    organisms = sorted([r[0] for r in result.all()])

    return ApiResponse(
        data={
            "sourceTypes": [e.value for e in SourceType],
            "reviewStatuses": [e.value for e in ReviewStatus],
            "directions": [e.value for e in Direction],
            "organisms": organisms,
            "downloadFields": [
                "primaryName", "organismName", "sequence", "uniprotId",
                "ecNumber", "reactionEquation", "direction",
                "smiles", "chebiId", "averageMass",
                "geneName", "genbankId",
                "doi", "pubmedId",
            ],
        }
    )
