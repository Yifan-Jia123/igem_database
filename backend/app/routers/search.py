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


# The shape of an EC prefix: 1-4 numeric segments, dot-separated (`4` / `4.2` /
# `4.2.3` / `4.2.3.77`).
EC_PREFIX_PATTERN = re.compile(r"^\d+(\.\d+){0,3}$")


def _parse_ec_prefixes(raw: Optional[List[str]]) -> Tuple[List[List[str]], Optional[str]]:
    """Parse an EC prefix string like `4.2.3` into a segment list, with validation.

    Returns ``(prefix list, first invalid value)``. An invalid value **must raise,
    not be silently dropped** -- "the toolbar shows a filter, but the SQL does not
    actually filter" is exactly the class of defect this round removes, and dropping
    silently recreates it verbatim (the user sees a result set they cannot explain).

    An empty string / pure whitespace means "no filter", not an error -- the
    frontend sends an empty string when a filter box is cleared.
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
    # `le` and the frontend `searchTableEnzymes` default limit are **a pair**;
    # changing one requires changing the other (the frontend asking 3000 while this
    # is 2000 only yields a baffling 422). `ge=1` was added this round: there was no
    # lower bound, so limit=0 / negatives could get in, producing `LIMIT 0` or
    # syntactically broken SQL. The default 500 stays -- real callers all pass it
    # explicitly.
    limit: int = Query(500, ge=1, le=2000, description="一次取回的行数上限"),
    db: AsyncSession = Depends(get_db),
):
    """Aggregated enzyme rows for the table-form search results page.

    ``source_types`` is the **search set** (search scope); ``display_*`` are the
    three **display filters** on the results toolbar. Both are compiled into the
    fetch SQL (AND-combined), because "truncate by score first, then filter on the
    client" yields "the ones among the top N that happen to belong to that source",
    unrelated to the true answer. See ``search_enzyme_table``.

    ``total`` is the **true hit count before LIMIT** (not the count on the page), so
    the page can honestly say "2000 of 55712". The total for a multi-word query is
    real too -- each condition's fetch cap is raised to ``MULTI_CONDITION_CAP``, or
    it would be truncated the same way.
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
