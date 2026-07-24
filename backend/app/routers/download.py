from fastapi import APIRouter

from app.schemas.common import ApiResponse
from app.schemas.download import DownloadPreviewRequest, DownloadPreviewResponse, DownloadCreateRequest

router = APIRouter()


@router.post("/download/preview")
async def download_preview(request: DownloadPreviewRequest):
    """Preview download columns and row count (stub)."""
    return ApiResponse(data=DownloadPreviewResponse(
        columns=request.fields,
        row_count=len(request.items),
        estimated_file_name=f"download.{request.format}",
    ).model_dump(by_alias=True))


@router.post("/download/files")
async def download_files(request: DownloadCreateRequest):
    """Generate download file (stub)."""
    return ApiResponse(data={"status": "stub", "fileUrl": None})
