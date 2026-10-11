from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db
from app.schemas.common import ApiResponse
from app.services.entity_service import load_enzyme_detail

router = APIRouter()


@router.get("/enzymes/{enzyme_id}")
async def get_enzyme_detail(
    enzyme_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Assembled in `app.services.entity_service.load_enzyme_detail`, shared with `/bundle`."""
    detail = await load_enzyme_detail(db, enzyme_id)
    if detail is None:
        return ApiResponse(success=False, error={"code": "NOT_FOUND", "message": f"Enzyme {enzyme_id} not found"})
    return ApiResponse(data=detail.model_dump(by_alias=True))
