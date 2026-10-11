from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db
from app.schemas.common import ApiResponse
from app.services.entity_service import load_reaction_detail

router = APIRouter()


@router.get("/reactions/{reaction_id}")
async def get_reaction_detail(
    reaction_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Assembled in `app.services.entity_service.load_reaction_detail`, shared with `/bundle`."""
    detail = await load_reaction_detail(db, reaction_id)
    if detail is None:
        return ApiResponse(success=False, error={"code": "NOT_FOUND", "message": f"Reaction {reaction_id} not found"})
    return ApiResponse(data=detail.model_dump(by_alias=True))
