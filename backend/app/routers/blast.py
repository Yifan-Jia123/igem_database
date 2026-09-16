from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db
from app.schemas.blast import BlastSearchRequest
from app.schemas.common import ApiResponse
from app.services.blast_service import BlastToolMissingError, run_blast_search

router = APIRouter()


@router.post("/blast/search")
async def blast_search(request: BlastSearchRequest, db: AsyncSession = Depends(get_db)):
    try:
        payload = await run_blast_search(db, request)
    except BlastToolMissingError as exc:
        return ApiResponse(success=False, error={"code": "BLAST_NOT_INSTALLED", "message": str(exc)})
    except ValueError as exc:
        return ApiResponse(success=False, error={"code": "BAD_REQUEST", "message": str(exc)})
    except RuntimeError as exc:
        return ApiResponse(success=False, error={"code": "BLAST_FAILED", "message": str(exc)})

    return ApiResponse(data=payload.model_dump(by_alias=True))
