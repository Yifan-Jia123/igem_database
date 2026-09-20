import re

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, List, Tuple

from app.deps import get_db
from app.schemas.common import ApiResponse
from app.schemas.search import PathwaySearchRequest, TableByIdsRequest
from app.services.search_service import search_entries, search_enzyme_table, search_enzyme_table_by_ids
from app.services.pathway_service import search_pathways as do_pathway_search, PathwayInputError

router = APIRouter()


@router.get("/search/entries")
async def search_entries_endpoint(
    q: str = Query(..., description="查询词"),
    input_type: Optional[str] = Query(None),
    view_mode: str = Query("table"),
    organism_name: Optional[str] = Query(None),
    source_types: Optional[List[str]] = Query(None),
    review_statuses: Optional[List[str]] = Query(None),
    page: int = Query(1),
    page_size: int = Query(20),
    sort_by: Optional[str] = Query(None),
    sort_order: str = Query("asc"),
    db: AsyncSession = Depends(get_db),
):
    cards, pagination, graph_highlights = await search_entries(
        db,
        q=q,
        input_type=input_type,
        view_mode=view_mode,
        organism_name=organism_name,
        source_types=source_types,
        review_statuses=review_statuses,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_order=sort_order,
    )

    data = {
        "items": [c.model_dump(by_alias=True) for c in cards],
        "pagination": pagination.model_dump(by_alias=True),
    }
    if graph_highlights:
        data["graphHighlights"] = graph_highlights

    return ApiResponse(data=data)


# EC 前缀的形状: 1–4 段数字, 点号分隔(`4` / `4.2` / `4.2.3` / `4.2.3.77`)。
EC_PREFIX_PATTERN = re.compile(r"^\d+(\.\d+){0,3}$")


def _parse_ec_prefixes(raw: Optional[List[str]]) -> Tuple[List[List[str]], Optional[str]]:
    """把 `4.2.3` 这样的 EC 前缀串解析成段列表, 顺带校验。

    返回 ``(前缀列表, 第一个非法值)``。非法值**必须报错, 不能静默丢弃** ——
    「工具栏显示筛了、SQL 其实没筛」正是本轮要消灭的那类缺陷,
    静默丢弃就是把它原样再造一遍(用户会看到一个自己解释不了的结果集)。

    空串/纯空白视为「不过滤」, 不算错 —— 前端把筛选框清空时会传空串。
    """
    prefixes: List[List[str]] = []
    for token in raw or []:
        value = token.strip()
        if not value:
            continue
        if not EC_PREFIX_PATTERN.match(value):
            return [], value
        prefixes.append(value.split("."))
    return prefixes, None


@router.get("/search/table")
async def search_table_endpoint(
    q: str = Query(..., description="查询词"),
    input_type: Optional[str] = Query(None),
    source_types: Optional[List[str]] = Query(None, description="搜索集：只在圈定的来源里检索"),
    display_source_types: Optional[List[str]] = Query(
        None, description="显示筛选：数据来源（与搜索集是两层，两者 AND 叠加）"
    ),
    display_organism_names: Optional[List[str]] = Query(None, description="显示筛选：物种"),
    display_ec_prefixes: Optional[List[str]] = Query(None, description="显示筛选：EC 前缀，如 4.2.3"),
    # `le` 与前端 `searchTableEnzymes` 的默认 limit 是**一对**, 改一个必须改另一个
    # (前端要 3000 而这里是 2000 只会得到一个看不懂的 422)。
    # `ge=1` 是本次补的: 原来没有下界, limit=0 / 负数能传进来, 生成 `LIMIT 0`
    # 或语法坏的 SQL。默认 500 保持 —— 真实调用方都显式传。
    limit: int = Query(500, ge=1, le=2000, description="一次取回的行数上限"),
    db: AsyncSession = Depends(get_db),
):
    """Aggregated enzyme rows for the table-form search results page.

    ``source_types`` 是**搜索集**(检索范围)；``display_*`` 是结果页工具栏那三个
    **显示筛选**。两者都编进取数 SQL（AND 叠加），因为「先按分数截断、再在客户端筛」
    筛出来的是「前 N 名里恰好属于该来源的那几个」，与真实答案无关。
    见 ``search_enzyme_table`` 的说明。

    ``total`` 是 **LIMIT 之前的真实命中数**（不是页内条数），所以页面可以诚实地写
    「2000 of 55712」。多词查询的 total 也是真的 —— 每个条件的取数上限已提到
    ``MULTI_CONDITION_CAP``，否则它同样会被截断。
    """
    ec_prefixes, invalid_ec = _parse_ec_prefixes(display_ec_prefixes)
    if invalid_ec is not None:
        return ApiResponse(
            success=False,
            error={
                "code": "invalid_ec_prefix",
                "message": (
                    f'"{invalid_ec}" is not a valid EC prefix '
                    "(digits and dots only, 1 to 4 segments, e.g. 4.2.3)."
                ),
                "details": {"value": invalid_ec},
            },
        )

    items, total = await search_enzyme_table(
        db,
        q=q,
        input_type=input_type,
        source_types=source_types,
        limit=limit,
        display_source_types=display_source_types,
        display_organism_names=display_organism_names,
        display_ec_prefixes=ec_prefixes,
    )
    return ApiResponse(data={
        "items": [c.model_dump(by_alias=True) for c in items],
        "total": total,
    })


@router.post("/search/table/by-ids")
async def search_table_by_ids_endpoint(
    request: TableByIdsRequest,
    db: AsyncSession = Depends(get_db),
):
    """Aggregated enzyme rows for an explicit enzyme-id list (order preserved).

    Lets BLAST hits render through the same table-form result surface as
    keyword search, so the data-source / organism / EC filter sidebar operates
    on identical rich rows. ``total`` mirrors the number of input ids.
    """
    items, total = await search_enzyme_table_by_ids(db, enzyme_ids=request.enzyme_ids)
    return ApiResponse(data={
        "items": [c.model_dump(by_alias=True) for c in items],
        "total": total,
    })


@router.post("/search/pathways")
async def search_pathways_endpoint(
    request: PathwaySearchRequest,
    db: AsyncSession = Depends(get_db),
):
    """Pathway-mode search: ordered start → (…via…) → end compound chains.

    Returns the top distinct pathways (each a unique compound chain) with a
    ``graph`` that is the union of every returned pathway's elements (nodes,
    single edges and composite edge groups), so the client can paint the whole
    result set at once and highlight one pathway at a time.
    """
    try:
        cards, payload, total = await do_pathway_search(
            db,
            start_compound_id=request.start_compound_id,
            end_compound_id=request.end_compound_id,
            via_compound_ids=request.via_compound_ids,
            max_steps=request.max_steps,
            source_types=request.source_types,
            review_statuses=request.review_statuses,
            limit=request.limit,
        )
    except PathwayInputError as exc:
        return ApiResponse(
            success=False,
            error={"code": exc.code, "message": exc.message, "details": exc.details},
        )

    return ApiResponse(data={
        "items": [c.model_dump(by_alias=True) for c in cards],
        "total": total,
        "graph": payload.model_dump(by_alias=True),
    })
