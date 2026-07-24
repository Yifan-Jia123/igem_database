from fastapi import APIRouter

from app.schemas.common import ApiResponse
from app.schemas.homology import HomologySearchRequest, HomologyJobStatus

router = APIRouter()


@router.post("/homology/search")
async def start_homology_search(request: HomologySearchRequest):
    """Start a BLAST homology search (stub)."""
    return ApiResponse(data=HomologyJobStatus(
        job_id="job_placeholder",
        status="queued",
    ).model_dump(by_alias=True))


@router.get("/homology/jobs/{job_id}")
async def get_homology_job(job_id: str):
    """Query BLAST job status (stub)."""
    return ApiResponse(data=HomologyJobStatus(
        job_id=job_id,
        status="finished",
        results=[],
    ).model_dump(by_alias=True))
