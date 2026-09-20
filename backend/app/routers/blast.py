from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db
from app.schemas.blast import BlastSearchRequest
from app.schemas.common import ApiResponse
from app.services.blast_service import (
    BlastToolMissingError,
    count_subjects,
    run_blast_search,
)

router = APIRouter()


@router.get("/blast/subjects")
async def blast_subjects(
    source_types: Optional[List[str]] = Query(None, description="搜索集：只数圈定来源里的主体"),
    db: AsyncSession = Depends(get_db),
):
    """当前搜索集下 BLAST 会拿多少条主体序列。

    抽屉要在**用户点 Run 之前**显示这个数（跑完那次有 ``searchedSubjects``，
    但换搜索集后那个数属于上一次运行）。与 ``_ensure_blast_db`` **同一组谓词** ——
    这个数与实际建库的条数必须一致，详见 ``blast_service.count_subjects``。
    """
    subjects = await count_subjects(db, source_types)
    return ApiResponse(data={"subjects": subjects})


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
