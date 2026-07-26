from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
import os

from app.deps import get_db
from app.schemas.common import ApiResponse
from app.schemas.download import DownloadPreviewRequest, DownloadCreateRequest
from app.services.download_service import preview, generate_file, DOWNLOADS_DIR

router = APIRouter()


@router.post("/download/preview")
async def download_preview(request: DownloadPreviewRequest, db: AsyncSession = Depends(get_db)):
    columns, row_count, filename = await preview(
        download_type=request.download_type,
        items=request.items,
        fields=request.fields,
        format=request.format,
        db=db,
    )
    return ApiResponse(data={
        "columns": columns,
        "rowCount": row_count,
        "estimatedFileName": filename,
    })


@router.post("/download/files")
async def download_files(request: DownloadCreateRequest, db: AsyncSession = Depends(get_db)):
    file_url, status = await generate_file(
        download_type=request.download_type,
        items=request.items,
        fields=request.fields,
        format=request.format,
        db=db,
        include_external_links=request.include_external_links,
        include_graph_image=request.include_graph_image,
    )
    return ApiResponse(data={
        "fileUrl": file_url,
        "status": status,
    })


@router.get("/downloads/{filename:path}")
async def serve_download(filename: str):
    filepath = os.path.join(DOWNLOADS_DIR, filename)
    if not os.path.isfile(filepath):
        return ApiResponse(success=False, error={"code": "NOT_FOUND", "message": f"File {filename} not found"})
    return FileResponse(filepath, filename=filename)
