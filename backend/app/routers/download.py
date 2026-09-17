import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db
from app.schemas.common import ApiResponse
from app.schemas.download import DownloadCreateRequest, DownloadPreviewRequest
from app.services.download_service import (
    DOWNLOADS_DIR,
    DownloadError,
    field_catalog,
    generate_file,
    preview,
)

router = APIRouter()

# Explicit content types. Falling back to `mimetypes.guess_type` serves a .csv as
# `application/vnd.ms-excel` on Windows, which makes the browser hand it to Excel
# instead of downloading it as text.
MEDIA_TYPES = {
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".txt": "text/plain; charset=utf-8",
    ".json": "application/json",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".fasta": "text/plain",
    ".zip": "application/zip",
}


@router.get("/download/fields")
async def download_fields():
    """The column picker's field universe, so the frontend never mirrors FIELD_MAP."""
    return ApiResponse(data=field_catalog())


@router.post("/download/preview")
async def download_preview(request: DownloadPreviewRequest, db: AsyncSession = Depends(get_db)):
    try:
        columns, row_count, filename, unknown = await preview(
            download_type=request.download_type,
            items=request.items,
            fields=request.fields,
            format=request.format,
            db=db,
            enzyme_items=request.enzyme_items,
        )
    except DownloadError as error:
        raise HTTPException(status_code=400, detail=str(error))

    return ApiResponse(data={
        "columns": columns,
        "rowCount": row_count,
        "estimatedFileName": filename,
        "unknownFields": unknown,
    })


@router.post("/download/files")
async def download_files(request: DownloadCreateRequest, db: AsyncSession = Depends(get_db)):
    try:
        file_url, status, stats, unknown = await generate_file(
            download_type=request.download_type,
            items=request.items,
            fields=request.fields,
            format=request.format,
            db=db,
            enzyme_items=request.enzyme_items,
        )
    except DownloadError as error:
        raise HTTPException(status_code=400, detail=str(error))

    return ApiResponse(data={
        "fileUrl": file_url,
        "status": status,
        "stats": stats,
        "unknownFields": unknown,
    })


def _resolve_within_downloads(filename: str) -> Optional[str]:
    """Absolute path for `filename`, or None if it escapes the downloads folder.

    `{filename:path}` hands us whatever the URL decoded to, so `..%2f..%2f.env`
    arrives here as a traversal. Resolve first, then require the result to stay
    under the real downloads root.
    """
    root = os.path.realpath(DOWNLOADS_DIR)
    candidate = os.path.realpath(os.path.join(root, filename))
    if candidate != root and not candidate.startswith(root + os.sep):
        return None
    return candidate


@router.get("/downloads/{filename:path}")
async def serve_download(filename: str):
    filepath = _resolve_within_downloads(filename)
    if filepath is None or not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail=f"File {filename} not found")

    extension = os.path.splitext(filepath)[1].lower()
    return FileResponse(
        filepath,
        filename=os.path.basename(filepath),
        media_type=MEDIA_TYPES.get(extension, "application/octet-stream"),
    )
