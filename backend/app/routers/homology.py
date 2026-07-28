from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db
from app.schemas.common import ApiResponse
from app.schemas.homology import HomologySearchRequest
from app.services.homology_service import get_homology_job as get_job, run_homology_search

router = APIRouter()


@router.post("/homology/search")
async def start_homology_search(request: HomologySearchRequest, db: AsyncSession = Depends(get_db)):
    try:
        status = await run_homology_search(db, request)
    except ValueError as exc:
        return ApiResponse(success=False, error={"code": "BAD_REQUEST", "message": str(exc)})

    return ApiResponse(data=status.model_dump(by_alias=True))


@router.get("/homology/jobs/{job_id}")
async def get_homology_job(job_id: str):
    status = get_job(job_id)
    if not status:
        return ApiResponse(success=False, error={"code": "NOT_FOUND", "message": f"Homology job {job_id} not found"})

    return ApiResponse(data=status.model_dump(by_alias=True))
