from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, distinct

from app.deps import get_db
from app.models import Enzyme, Reaction, Compound, SourceType, ReviewStatus, Direction, EnzymeReactionEdge
from app.schemas.common import ApiResponse

router = APIRouter()

# 这些模块的取值域是**全量**酶 (table 检索 / BLAST 结果表 / 下载)
_FULL_DOMAIN_MODULES = {"table", "blast", "enzyme_table", "download"}


@router.get("/metadata/filter-options")
async def get_filter_options(
    module: str = Query(None, description="当前模块"),
    db: AsyncSession = Depends(get_db),
):
    """各筛选器的可选值。``organisms`` 的取值域随 ``module`` 变。

    图 / 首页只用有反应注释的酶 (见 ``graph_service``), 所以它的物种下拉必须收窄到
    同一批酶 —— 否则下拉里会出现一个"选了图就空"的物种: 全量库里 96k 个酶有一万多个
    物种, 而能进图的只有一小部分。table / blast 的结果表是全量域, 保持全量物种。
    """
    organism_query = select(distinct(Enzyme.organism_name)).where(
        Enzyme.organism_name.isnot(None)
    )
    if module not in _FULL_DOMAIN_MODULES:
        organism_query = organism_query.where(
            Enzyme.enzyme_id.in_(select(EnzymeReactionEdge.enzyme_id))
        )
    result = await db.execute(organism_query)
    organisms = sorted([r[0] for r in result.all()])

    # 搜索集的候选项 = **真的有行的**来源 + 各自条数。
    # 选择器不能列出 0 行的集合: 选中就是空结果, 而用户看不出那是自己选的。
    # 今天恰好是 swiss_prot / trembl (另两个枚举值 0 行); 以后真有 ai_literature
    # 数据时会自动出现, 前端不必改。计数顺带喂给常驻指示模块显示成 "TrEMBL (94,334)"。
    # 走 idx_enzyme_source_review (source_type, review_status) 的前导列, 索引覆盖。
    source_rows = await db.execute(
        select(Enzyme.source_type, func.count()).group_by(Enzyme.source_type)
    )
    search_sets = sorted(
        (
            {"value": st.value if hasattr(st, "value") else str(st), "count": count}
            for st, count in source_rows.all()
            if st is not None
        ),
        key=lambda entry: entry["value"],
    )

    return ApiResponse(
        data={
            "sourceTypes": [e.value for e in SourceType],
            "searchSets": search_sets,
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
