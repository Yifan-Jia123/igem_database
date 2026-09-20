"""
Entry search service: multi-field weighted UNION search with AND/OR/NOT support.
"""

import hashlib
import re
from typing import List, NamedTuple, Optional, Tuple, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select
from sqlalchemy.sql import text as sa_text
from sqlalchemy.exc import ProgrammingError, OperationalError

from app.models import Enzyme, Gene, Reaction, EnzymeReactionEdge
from app.schemas.enzyme import EnzymeCard, TableEnzymeCard
from app.schemas.common import Pagination
from app.utils.query_parser import parse_query, SearchClause, SearchCondition, detect_input_type
from app.utils.compound_filters import EXCLUDED_COMMON_COMPOUND_IDS


# Fields sorted by weight (exact ID match → text match)
# alias: the SQL alias used in JOIN; used in WHERE {alias}.{column}
FIELD_CONFIG: Dict[str, dict] = {
    "enzyme_id":   {"table": "enzyme",   "alias": "e",   "column": "enzyme_id",     "weight": 100},
    "uniprot_id":  {"table": "enzyme",   "alias": "e",   "column": "uniprot_id",    "weight": 95},
    "rhea_id":     {"table": "reaction", "alias": "r",   "column": "rhea_id",       "weight": 90},
    "genbank_id":  {"table": "gene",     "alias": "g",   "column": "genbank_id",    "weight": 85},
    "compound_id": {"table": "compound", "alias": "cpd", "column": "compound_id",   "weight": 85},
    "chebi_id":    {"table": "compound", "alias": "cpd", "column": "chebi_id",      "weight": 85},
    "pubmed_id":   {"table": "evidence", "alias": "ev",  "column": "pubmed_id",     "weight": 80},
    "ec_number":   {"table": "reaction", "alias": "r",   "column": "ec_number",     "weight": 70},
    "primary_name":{"table": "enzyme",   "alias": "e",   "column": "primary_name",  "weight": 50},
    "enzyme_name": {"table": "enzyme",   "alias": "e",   "column": "primary_name",  "weight": 50},
    "compound_name": {"table": "compound", "alias": "cpd", "column": "name",        "weight": 50},
    "compound":    {"table": "compound", "alias": "cpd", "column": "name",          "weight": 50},
    "smiles":      {"table": "compound", "alias": "cpd", "column": "smiles",        "weight": 35},
    "formula":     {"table": "compound", "alias": "cpd", "column": "formula",       "weight": 35},
    "gene_name":   {"table": "gene",     "alias": "g",   "column": "gene_name",     "weight": 40},
    "organism":    {"table": "enzyme",   "alias": "e",   "column": "organism_name", "weight": 30},
    "species":     {"table": "enzyme",   "alias": "e",   "column": "organism_name", "weight": 30},
}

ALL_FIELDS = [
    "enzyme_id", "uniprot_id", "rhea_id", "genbank_id",
    "compound_id", "chebi_id", "ec_number", "primary_name",
    "compound_name", "gene_name", "organism",
]

SEARCH_INDEX_FIELD_MAP: Dict[str, List[str]] = {
    "enzyme_id": ["enzyme_id"],
    "uniprot_id": ["uniprot_id"],
    "uniprot": ["uniprot_id", "uniprot_url"],
    "entry_name": ["entry_name"],
    "rhea_id": ["rhea_id"],
    "ec_number": ["ec_number"],
    "genbank_id": ["accession"],
    "accession": ["accession"],
    "compound_id": ["compound_id", "chebi_id", "substrate_chebi", "product_chebi", "chebi_ids"],
    "chebi": ["compound_id", "chebi_id", "substrate_chebi", "product_chebi", "chebi_ids"],
    "chebi_id": ["compound_id", "chebi_id", "substrate_chebi", "product_chebi", "chebi_ids"],
    "compound_name": ["compound_name", "substrate", "product"],
    "compound": ["compound_name", "substrate", "product"],
    "primary_name": ["primary_name"],
    "enzyme_name": ["primary_name", "alternative_names", "entry_name"],
    "gene_name": ["gene_name"],
    "organism": ["organism"],
    "species": ["organism"],
    "pubmed_id": ["pubmed_id"],
    "doi": ["doi"],
    "reference": ["reference_title", "reference_authors", "journal", "year", "reference_type"],
    "go": ["go_id", "go_term"],
    "go_id": ["go_id"],
    "go_term": ["go_term"],
    "inchi_key": ["inchi_key"],
    "smiles": ["smiles", "reaction_smiles"],
    "sequence": ["canonical_sequence", "isoform_sequence", "accession"],
    "isoform": ["isoform_id", "isoform_sequence"],
}

SEARCH_INDEX_ALL_FIELDS = sorted({
    field
    for fields in SEARCH_INDEX_FIELD_MAP.values()
    for field in fields
} | {
    "enzyme_id", "uniprot_id", "entry_name", "organism", "primary_name",
    "alternative_names", "rhea_id", "ec_number", "reaction_equation",
    "reaction_direction", "reaction_smiles", "chebi_ids", "compound_id",
    "chebi_id", "compound_name", "substrate_chebi", "substrate",
    "product_chebi", "product", "pubmed_id", "doi", "reference_title",
    "reference_authors", "journal", "volume", "pages", "year",
    "reference_type", "evidence_positions", "reference_url", "go_id",
    "go_term", "go_url", "accession", "sequence_source", "molecule_type",
    "sequence_url", "inchi_key", "isoform_id", "isoform_length",
    "isoform_mass", "canonical_length", "canonical_mass",
    "canonical_sequence", "isoform_sequence", "smiles", "average_mass",
    "chebi_url", "uniprot_url",
})

# JOIN clauses for reaching enzyme table from each table
# Uses consistent aliases: e=enzyme, g=gene, r=reaction, cpd=compound, ev=evidence, ere=enzyme_reaction_edge, rc=reaction_compound
TABLE_JOIN = {
    "enzyme":   "",
    "gene":     "JOIN gene g ON e.enzyme_id = g.enzyme_id",
    "reaction": ("JOIN enzyme_reaction_edge ere ON e.enzyme_id = ere.enzyme_id "
                 "JOIN reaction r ON ere.reaction_id = r.reaction_id"),
    "compound": ("JOIN enzyme_reaction_edge ere ON e.enzyme_id = ere.enzyme_id "
                 "JOIN reaction_compound rc ON ere.reaction_id = rc.reaction_id "
                 "JOIN compound cpd ON rc.compound_id = cpd.compound_id"),
    "evidence": "JOIN evidence ev ON e.enzyme_id = ev.enzyme_id",
}

EXCLUDED_COMPOUND_SQL = ", ".join(f"'{cid}'" for cid in sorted(EXCLUDED_COMMON_COMPOUND_IDS))
TABLE_FILTER = {
    "compound": f" AND cpd.compound_id NOT IN ({EXCLUDED_COMPOUND_SQL}) AND cpd.name <> cpd.compound_id",
}


async def search_entries(
    db: AsyncSession,
    q: str,
    input_type: Optional[str] = None,
    view_mode: str = "table",
    organism_name: Optional[str] = None,
    source_types: Optional[List[str]] = None,
    review_statuses: Optional[List[str]] = None,
    page: int = 1,
    page_size: int = 20,
    sort_by: Optional[str] = None,
    sort_order: str = "asc",
) -> Tuple[List[EnzymeCard], Pagination, Optional[dict]]:

    if not input_type or input_type == "auto":
        detected = detect_input_type(q)
        if detected:
            input_type = detected

    clauses = parse_query(q)
    if not clauses:
        return [], Pagination(page=page, page_size=page_size, total=0, total_pages=0), None

    offset = (page - 1) * page_size
    scope = ScopeFilters(source_types=source_types, review_statuses=review_statuses)

    if len(clauses) == 1 and len(clauses[0].conditions) == 1:
        cond = clauses[0].conditions[0]
        scored = await _search_single(cond, input_type, page_size, offset, db, scope)
    else:
        scored = await _search_multi(clauses, input_type, page_size, offset, db, scope)

    enzyme_ids = [eid for eid, _ in scored.rows]

    if not enzyme_ids:
        return [], Pagination(page=page, page_size=page_size, total=0, total_pages=0), None

    cards = await _fetch_cards(db, enzyme_ids, organism_name)

    graph_highlights = None
    if view_mode == "graph":
        edge_ids = [c.edge_id for c in cards if c.edge_id]
        if edge_ids:
            graph_highlights = {"highlightedEdgeIds": edge_ids}

    # ⚠️ 刻意**不用** ``scored.total``: 这里的 total 是「页内条数」, 被
    # ``Pagination.total`` / ``total_pages`` 用着, 改它等于动分页显示与图范围选择的
    # 既有行为 —— 与本次要修的「表格页显示」无关, 不混进来。
    total = len(enzyme_ids)
    total_pages = max(1, (total + page_size - 1) // page_size)

    return (
        cards,
        Pagination(page=page, page_size=page_size, total=total, total_pages=total_pages),
        graph_highlights,
    )


def _ec_sort_key(ec: str) -> Tuple[int, ...]:
    """Sort EC strings numerically (4.2.3.77 → (4, 2, 3, 77))."""
    return tuple(int(part) for part in re.findall(r"\d+", ec))


async def search_enzyme_table(
    db: AsyncSession,
    q: str,
    input_type: Optional[str] = None,
    source_types: Optional[List[str]] = None,
    limit: int = 500,
    display_source_types: Optional[List[str]] = None,
    display_organism_names: Optional[List[str]] = None,
    display_ec_prefixes: Optional[List[List[str]]] = None,
) -> Tuple[List[TableEnzymeCard], int]:
    """Search for enzymes and aggregate each enzyme's reaction EC numbers.

    Used by the table-form search results page. Returns one ``TableEnzymeCard``
    per matched enzyme (ordered by the same relevance scoring as
    ``search_entries``) where ``ec_numbers``/``source_types`` cover every
    reaction edge of that enzyme instead of a single representative edge.

    ``source_types`` 是**搜索集**(search set): 它收窄的是**检索范围**本身,
    由 ``_scoped_query`` 编进取数 SQL。
    ``display_*`` 是结果页工具栏那三个**显示筛选**(来源 / 物种 / EC 前缀)——
    它们同样编进取数 SQL, 因为**这三个筛选必须是同一条 SQL 的一部分**:
    先按 ``ORDER BY score DESC LIMIT n`` 截断、再在客户端筛, 筛出来的是
    「分数前 n 名里恰好属于该来源的那几个」, 与真实答案毫无关系。实测
    `terpene synthase` 在 limit=600 下客户端筛完只剩 **2** 条 swiss_prot,
    limit=2000 剩 **3** 条, 而服务端圈定给的是 **1,243** 条。
    方向还会随查询翻转(`synthase` 留 98.5% swiss_prot), 因为 score 与「来源」这个
    维度毫不相关 —— 所以「提高上限」只能缩小偏差, 消除不了它。

    返回的 ``total`` 是 **LIMIT 之前的真实命中数**(``COUNT(*) OVER ()``),
    与 ``len(cards)`` 是两件事 —— 后者被 ``limit`` 截过。筛选之后若命中集仍超过
    ``limit``, 页面会显示「2000 of 55712」这样的形态, 那正是诚实的读法。

    ⚠️ ``display_*`` 与 ``source_types`` **必须保持是两组参数**: 前者只属于检索结果页,
    后者在 App 里跨页保持且 BLAST / 图谱也认它。看混了就会重演
    「调用时省掉实参 -> 谓词退化为空串」那次事故(见下)。
    """
    if not input_type or input_type == "auto":
        detected = detect_input_type(q)
        if detected:
            input_type = detected

    clauses = parse_query(q)
    if not clauses:
        return [], 0

    scope = ScopeFilters(
        source_types=source_types,
        display_source_types=display_source_types,
        organism_names=display_organism_names,
        ec_prefixes=display_ec_prefixes,
    )

    if len(clauses) == 1 and len(clauses[0].conditions) == 1:
        cond = clauses[0].conditions[0]
        scored = await _search_single(cond, input_type, limit, 0, db, scope, with_total=True)
    else:
        scored = await _search_multi(clauses, input_type, limit, 0, db, scope)

    enzyme_ids = [eid for eid, _ in scored.rows]
    if not enzyme_ids:
        return [], scored.total

    cards = await _aggregate_table_cards(db, enzyme_ids)
    return cards, scored.total


async def search_enzyme_table_by_ids(
    db: AsyncSession,
    enzyme_ids: List[str],
) -> Tuple[List[TableEnzymeCard], int]:
    """Aggregate table rows for an explicit enzyme-id list, order preserved.

    Used to render BLAST hits through the same table-form result surface as
    keyword search: pass the hit enzyme ids in E-value order and get one rich
    ``TableEnzymeCard`` per id (duplicates in the input produce duplicate
    rows, mirroring per-subject BLAST hits) with the same per-enzyme EC /
    data-source / reaction-count aggregation as ``search_enzyme_table``.
    """
    ids = [eid for eid in enzyme_ids if eid]
    if not ids:
        return [], 0
    cards = await _aggregate_table_cards(db, ids)
    return cards, len(ids)


async def _aggregate_table_cards(
    db: AsyncSession,
    enzyme_ids: List[str],
) -> List[TableEnzymeCard]:
    """Bulk-fetch enzymes/gene names/edges and aggregate one row per enzyme.

    ``enzyme_ids`` keeps its caller-provided order (duplicates allowed); rows
    whose enzyme no longer exists are skipped.
    """
    result = await db.execute(select(Enzyme).where(Enzyme.enzyme_id.in_(enzyme_ids)))
    enzymes = {e.enzyme_id: e for e in result.scalars().all()}
    gene_names = await _load_gene_names(db, enzyme_ids)

    edge_result = await db.execute(
        select(EnzymeReactionEdge, Reaction)
        .join(Reaction, EnzymeReactionEdge.reaction_id == Reaction.reaction_id)
        .where(EnzymeReactionEdge.enzyme_id.in_(enzyme_ids))
    )

    ec_by_enzyme: Dict[str, set] = {eid: set() for eid in enzyme_ids}
    source_by_enzyme: Dict[str, set] = {eid: set() for eid in enzyme_ids}
    reaction_by_enzyme: Dict[str, set] = {eid: set() for eid in enzyme_ids}

    # 先用酶**自身**的 source_type 播种。**不能只从反应边推来源** —— 没有反应注释的酶
    # (全量 TrEMBL 下是绝大多数) 边集为空, 推出来的 source_types 就是 [], 而检索页的
    # 来源筛选是按 `row.sourceTypes.some(...)` 匹配的 (SearchResultsPage.tsx:363),
    # 于是「一选来源就把无边酶整批隐藏」: 看着像筛掉了别的来源, 实际是把全量域
    # 偷换成了有边的子集 (实测 4790 个酶里只有 124 个有边)。
    # 边来源照样并入, docstring 承诺的「覆盖每条边」不受影响 —— 实测两者 0 处不一致
    # (边的来源本来就是 ETL 从 enzyme 表读来的)。
    for eid, enz in enzymes.items():
        if enz.source_type:
            source_by_enzyme.setdefault(eid, set()).add(_enum_value(enz.source_type))

    for row in edge_result.all():
        edge, react = row
        eid = edge.enzyme_id
        if eid not in ec_by_enzyme:
            continue
        reaction_by_enzyme[eid].add(edge.reaction_id)
        if react and react.ec_number:
            ec_by_enzyme[eid].add(react.ec_number.strip())
        if edge.source_type:
            source_by_enzyme[eid].add(_enum_value(edge.source_type))

    cards: List[TableEnzymeCard] = []
    for eid in enzyme_ids:
        enz = enzymes.get(eid)
        if not enz:
            continue
        cards.append(TableEnzymeCard(
            enzyme_id=eid,
            primary_name=enz.primary_name,
            uniprot_id=enz.uniprot_id,
            organism_name=enz.organism_name,
            gene_name=gene_names.get(eid),
            ec_numbers=sorted(ec_by_enzyme[eid], key=_ec_sort_key),
            source_types=sorted(source_by_enzyme[eid]),
            reaction_count=len(reaction_by_enzyme[eid]),
        ))

    return cards


def _enum_value(value) -> str:
    """ENUM 列取回来可能是枚举成员, 也可能是裸字符串。"""
    return value.value if hasattr(value, "value") else str(value)


# 多条件检索时**每个条件**的取数上限。全库最大可能的命中集是酶表总行数
# (95,869), 所以这个数等价于「不截断」。原来写死 10000, 于是
# `terpene synthase` 这类**多词查询**(parse_query 的 OR 语义会拆成两个 clause)
# 的命中集被硬顶在 20,000 —— 提高 `limit` 永远到不了真实集合,
# 窗口函数数出来的 total 也会是截断后的假数。
# 实测代价: 同一个三段聚合, LIMIT 600 是 7.896s, 不限行数把全部 37,150 个 id
# 取回来是 8.144s —— 代价全在 `%词%` 的全表扫上, 与返回行数无关。
MULTI_CONDITION_CAP = 300_000

# 物种筛选里「没有物种」那一项的哨兵。前端把空 organism_name 显示成
# 'Unknown organism' 当成一个可选项, 但服务端按精确串相等去匹配,
# 于是「选它反而什么都筛不到」。哨兵让这一项能翻译成
# `organism_name IS NULL OR organism_name = ''`。当前库里 0 行 NULL、0 行空串,
# 所以这是**潜伏**缺陷的关闭, 不是当下可见的修复。
UNKNOWN_ORGANISM_SENTINEL = "__unknown__"


class ScopeFilters(NamedTuple):
    """一次检索要编进 SQL 的**圈定谓词**的全部输入。

    ``source_types`` / ``review_statuses`` 是**搜索集**(检索范围);
    其余三个是检索页工具栏的**显示筛选**(来源 / 物种 / EC)。
    两者在 SQL 里 **AND 叠加** —— 搜索集=trembl 且显示筛选=swiss_prot 时结果是 0 行,
    那是两层叠加的正确结果, 不是冲突(前端会先把这种组合渲染成 disabled 并用文案说明)。

    两者**必须保持是两个参数**: 生命周期与影响面都不同 —— 搜索集在 App 里跨页保持,
    BLAST 与图谱也认它; 显示筛选只属于检索结果页。
    """

    source_types: Optional[List[str]] = None
    review_statuses: Optional[List[str]] = None
    display_source_types: Optional[List[str]] = None
    organism_names: Optional[List[str]] = None
    ec_prefixes: Optional[List[List[str]]] = None


class ScoredEnzymes(NamedTuple):
    """一条检索路径的返回: 本页的行 + **真实命中总数**。

    ``total`` 来自 ``COUNT(*) OVER ()``(见 ``_scoped_query``), 是 LIMIT **之前**的
    去重酶数, 与 ``len(rows)`` 是两件事 —— 后者被 ``limit`` 截过。
    多条件路径的 ``total`` 是 ``len(merged)``, 在 ``MULTI_CONDITION_CAP`` 之下即精确值。

    ``/search/entries`` **不用** ``total``(它的分页语义保持 ``len(enzyme_ids)`` 不变),
    只有表格检索用它 —— 见 ``search_enzyme_table``。
    """

    rows: List[Tuple[str, int]]
    total: int


def _scope_filter_sql(scope: ScopeFilters, left: str = "enzyme_id") -> Tuple[str, dict]:
    """把圈定谓词(搜索集 + 显示筛选)编成一个**布尔条件**, 由调用方套在取数查询上。

    为什么必须进 SQL, 而不是像 organism 那样在 ``_fetch_cards`` 里对当前页做后置过滤:
    后置过滤时 LIMIT/OFFSET 仍按**未过滤**的全集切页, 于是
    「一页 20 条里恰好属于该来源的 0~2 条」= 用户看到几乎空白的结果页,
    翻页还会漏掉/重复条目; ``select_scope_enzymes`` 拿 200 条候选去选图范围时,
    也只是「未过滤的前 200 里属于该来源的那些」, 图的范围被静默缩窄。
    写进 SQL 之后, 分页是对**过滤后的集合**切分, 与用户看到的一致。

    对**表格检索**而言这条更尖锐: 取数上限 2000, 而后置筛选的代价不是「少几条」而是
    「按分数截断的那 2000 条里恰好有几个是 Swiss-Prot」—— 实测 `terpene synthase` 在
    limit=600 下客户端筛完只剩 2 条, 而服务端圈定给的是 1,243 条。

    判定条件是 ``enzyme`` 表**自身**的 source_type / review_status —— 全量 TrEMBL 下
    绝大多数酶没有反应边, 若按边过滤就等于把无边酶整批删掉 (取数域被偷换)。

    返回的是**条件本身**(不含 WHERE), 由调用方拼进它自己的 WHERE ——
    索引路径拼进三段扫描的段内(见 ``_search_single_index``),
    legacy 路径同样拼进每一段。

    ``left`` 是 IN 左侧那一列**所属的表别名**。索引路径的 ``search_index`` 只有一列
    ``enzyme_id``, 不写表名也不会有歧义; 但 legacy 的三段本来就把 ``enzyme`` 与
    ``enzyme_reaction_edge`` 都 JOIN 进来了 —— **两张表都有 ``enzyme_id``**,
    裸写会直接报 ``(1052) Column 'enzyme_id' ... is ambiguous``。
    所以 legacy 侧必须传 ``left="e.enzyme_id"``。
    """
    conditions: List[str] = []
    bind_params: dict = {}

    def in_condition(column: str, values: List[str], prefix: str) -> str:
        placeholders = []
        for i, item in enumerate(values):
            name = f"{prefix}{i}"
            bind_params[name] = str(item)
            placeholders.append(f":{name}")
        return f"e.{column} IN ({', '.join(placeholders)})"

    # 搜索集与「Data source」显示筛选**各自**是一条 IN 条件, 两条之间是 AND。
    # 不能把两个值表并成一个 IN —— 那是并集(放宽), 而两层叠加要的是交集:
    # 搜索集=trembl 且显示筛选=swiss_prot 必须筛出 0 行。
    if scope.source_types:
        conditions.append(in_condition("source_type", scope.source_types, "scope_src"))
    if scope.display_source_types:
        conditions.append(in_condition("source_type", scope.display_source_types, "disp_src"))
    if scope.review_statuses:
        conditions.append(in_condition("review_status", scope.review_statuses, "scope_rev"))

    if scope.organism_names:
        # 物种是精确串相等(前端 `organismSet.has(row.organismName)`),
        # NULL / 空串两边都恒不匹配 —— 除了那个哨兵项, 它专门指代这两类行。
        known = [n for n in scope.organism_names if n != UNKNOWN_ORGANISM_SENTINEL]
        parts: List[str] = []
        if known:
            parts.append(in_condition("organism_name", known, "disp_org"))
        if len(known) != len(scope.organism_names):
            parts.append("(e.organism_name IS NULL OR e.organism_name = '')")
        conditions.append("(" + " OR ".join(parts) + ")")

    if scope.ec_prefixes:
        # EC 前缀**必须**用逐段相等, 不能用 `LIKE '4.2.3%'` —— 后者会把
        # `4.2.30.1` 也匹配进来(把 `30` 的 `3` 当成前缀)。要用 LIKE 就得写成
        # `LIKE '4.2.3.%'`, 少写一个点就静默放宽, 所以选 SUBSTRING_INDEX。
        #
        # 判据与前端 `prefixMatches`(SearchResultsPage.tsx) 逐段相等同一口径,
        # 且**必须**走「边 ⋈ reaction」而不是 search_index 里的 ec 字段 ——
        # 前端行上的 ecNumbers 就是从边来的, 没有反应边的酶两边都不匹配。
        ec_parts: List[str] = []
        for i, segments in enumerate(scope.ec_prefixes):
            if not segments:
                continue
            val_name, len_name = f"disp_ec{i}", f"disp_ec_len{i}"
            bind_params[val_name] = ".".join(segments)
            bind_params[len_name] = len(segments)
            ec_parts.append(
                f"SUBSTRING_INDEX(TRIM(r.ec_number), '.', :{len_name}) = :{val_name}"
            )
        if ec_parts:
            conditions.append(
                "e.enzyme_id IN ("
                "SELECT DISTINCT ed.enzyme_id FROM enzyme_reaction_edge ed "
                "JOIN reaction r ON r.reaction_id = ed.reaction_id "
                "WHERE r.ec_number IS NOT NULL AND ("
                + " OR ".join(ec_parts)
                + "))"
            )

    if not conditions:
        return "", {}

    return (
        f"{left} IN (SELECT e.enzyme_id FROM enzyme e WHERE "
        + " AND ".join(conditions)
        + ")",
        bind_params,
    )


def _scoped_query(
    aggregated_sql: str,
    scope_cond: str,
    limit: int,
    offset: int,
    with_total: bool = False,
) -> str:
    """把「已聚合到每个酶一行」的查询套上圈定谓词 + 排序 + 分页(+ 可选真实总数)。

    无筛选且不要 total 时**原样返回**单层写法(那条路径的 EXPLAIN 是实测过的,
    不引入多余的派生表); 否则多套一层: 聚合先跑, 半连接只对去重后的少量酶做一次。

    ``with_total`` 把 ``COUNT(*) OVER ()`` 加在外层 select 上。**必须在外层**:
    窗口函数在 GROUP BY **之后**、LIMIT **之前**求值, 所以桶恰好是「去重后的酶」,
    数出来的就是真实命中数; 放进里面的聚合 select 数到的会是聚合**之前**的行。
    实测这个窗口函数是免费的(7.896s vs 7.913s), 代价由 `%词%` 的全表扫决定。

    ``with_total=False`` 时 SQL 与改造前**逐字相同** —— ``/search/entries`` 走的就是
    这条路径, 它的分页语义要保持不变。
    """
    if not scope_cond and not with_total:
        return f"{aggregated_sql} ORDER BY score DESC LIMIT {limit} OFFSET {offset}"
    columns = "enzyme_id, score, COUNT(*) OVER () AS total" if with_total else "enzyme_id, score"
    where = f"WHERE {scope_cond} " if scope_cond else ""
    return (
        f"SELECT {columns} FROM ({aggregated_sql}) scoped "
        f"{where}"
        f"ORDER BY score DESC LIMIT {limit} OFFSET {offset}"
    )


async def _search_single(
    cond: SearchCondition,
    input_type: Optional[str],
    limit: int,
    offset: int,
    db: AsyncSession,
    scope: Optional[ScopeFilters] = None,
    with_total: bool = False,
) -> ScoredEnzymes:
    """Search for one condition, return this page's ``(enzyme_id, score)`` + true total."""

    value = _normalized_search_value(cond, input_type)
    if value.upper() in EXCLUDED_COMMON_COMPOUND_IDS:
        return ScoredEnzymes([], 0)

    scope = scope or ScopeFilters()
    scope_sql, scope_params = _scope_filter_sql(scope)

    if await _search_index_ready(db):
        indexed = await _search_single_index(
            cond, input_type, limit, offset, db, value, scope_sql, scope_params, with_total
        )
        # 空结果**且没有圈定谓词**时才回退到 legacy(索引可能不覆盖某个字段)。
        # 有筛选时不能回退: 空结果在筛选下是**正常的用户可见状态**(用户圈了一个
        # 没有命中的物种), 把它当成「索引不覆盖」的信号会白跑一次更贵的全表扫 ——
        # 实测那正好是「筛不出东西时反而等两倍时间」。
        if indexed.rows or scope_sql:
            return indexed

    return await _search_single_legacy(
        cond, input_type, limit, offset, db, value, scope, with_total
    )


def _normalized_search_value(cond: SearchCondition, input_type: Optional[str]) -> str:
    value = cond.value.strip()
    field = (cond.field or "").lower()
    if (
        input_type in {"compound_id", "chebi_id"}
        and field in {"chebi", "chebi_id", "compound_id"}
        and re.fullmatch(r"\d+", value)
    ):
        return f"CHEBI:{value}"
    return value


def _index_hash(value: str) -> str:
    """与 ETL 写入侧**同一个函数**: sha1(value.lower()) 的十六进制。

    必须与 etl_search_index._hash 逐字一致 —— 写入侧算一次、查询侧再算一次来命中
    `idx_search_index_hash`。两侧的 lower() 都必须是 Python 的 str.lower()
    (不是 MySQL 的 LOWER()): 实测全库 137,995 行的 field_value_hash
    与 SHA1(LOWER(field_value)) 完全一致, 所以两者在现有语料上等价。
    """
    return hashlib.sha1(value.lower().encode("utf-8")).hexdigest()


def _escape_like(value: str) -> str:
    """把用户在 LIKE 里输入的 % 和 _ 转义成字面量。

    这两个字符在 LIKE 里是通配符, 而本库的检索值里 `_` 很常见
    (RefSeq accession 如 `NP_004453.3`) —— 不转义就会把「精确前缀」搜成
    「任意单字符」, 结果偏宽且看起来像是正常的模糊匹配。
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _scored(
    db: AsyncSession,
    aggregated_sql: str,
    scope_cond: str,
    bind_params: dict,
    rows,
    offset: int,
    with_total: bool,
) -> ScoredEnzymes:
    """把一次取数的结果行包成 ``ScoredEnzymes``。

    ``with_total`` 时外层 select 多一列 ``COUNT(*) OVER ()``, 它是 LIMIT **之前**的
    去重酶数。窗口函数只附在**取回的那些行**上, 所以空页没有行可依附:
    ``offset=0`` 的空页等价于 0 命中, 直接返回 0; 带 offset 的调用方(目前没有)则
    单独数一次 —— 如实去数, 免得把 total 静默报成 0。
    """
    pairs = [(row[0], row[1]) for row in rows]
    if not with_total:
        return ScoredEnzymes(pairs, len(pairs))
    if rows:
        return ScoredEnzymes(pairs, int(rows[0][2]))
    if offset == 0:
        return ScoredEnzymes(pairs, 0)
    count_sql = f"SELECT COUNT(*) FROM ({aggregated_sql}) scoped"
    if scope_cond:
        count_sql += f" WHERE {scope_cond}"
    total = (await db.execute(sa_text(count_sql), bind_params)).scalar()
    return ScoredEnzymes(pairs, int(total or 0))


async def _search_single_index(
    cond: SearchCondition,
    input_type: Optional[str],
    limit: int,
    offset: int,
    db: AsyncSession,
    value: str,
    scope_sql: str = "",
    scope_params: Optional[dict] = None,
    with_total: bool = False,
) -> ScoredEnzymes:
    scope_params = scope_params or {}

    if input_type and input_type in SEARCH_INDEX_FIELD_MAP:
        fields_to_search = SEARCH_INDEX_FIELD_MAP[input_type]
    elif cond.field and cond.field in SEARCH_INDEX_FIELD_MAP:
        fields_to_search = SEARCH_INDEX_FIELD_MAP[cond.field]
    else:
        fields_to_search = SEARCH_INDEX_ALL_FIELDS

    field_params = {}
    field_placeholders = []
    for idx, field_name in enumerate(fields_to_search):
        param_name = f"field_{idx}"
        field_params[param_name] = field_name
        field_placeholders.append(f":{param_name}")

    field_filter = ""
    if field_placeholders:
        field_filter = f"AND field_name IN ({', '.join(field_placeholders)})"

    # 圈定谓词进每一段（理由见下）；无圈定时与原写法逐字相同。
    # 安全前提: field_filter 要么是 `AND field_name IN (...)`、要么是空串, 所以拼出来
    # 永远是一个合法的谓词序列, 不会出现悬空的 AND。
    scan_filter = f"{field_filter}\n              AND {scope_sql}" if scope_sql else field_filter

    # 三段 UNION ALL 的代价差别很大, 改法针对的是「索引能不能用上」:
    #
    #   精确段  -> `field_value_hash = :exact_hash`, 走 `idx_search_index_hash`。
    #              原来写的是 `LOWER(field_value) = LOWER(:v)`: 列被函数包住 + field_value
    #              是 TEXT 且无索引 -> 每次都全表扫。用 hash 等值既走索引, 又与写入侧
    #              同一套算法。**等价性有实测支撑**: 全库 distinct(field_value_hash) 与
    #              distinct(LOWER(field_value)) 都是 34,777, 且「同一 hash 对应多个不同
    #              小写值」的组数为 0 —— 即 hash 是小写值的单射, 等值匹配结果一致。
    #   前缀/包含段 -> 去掉 LOWER 包装, 交给列自身的 utf8mb4_unicode_ci 排序规则做
    #              大小写不敏感(LIKE 也遵循排序规则), 于是至少能用到 field_name 上的索引,
    #              而不是每行都跑一次 LOWER()。`%...%` 本身无法走 B-tree, 这是已知代价。
    #
    # 来源/状态/物种/EC 筛选推进**每一段扫描内**(`{scan_filter}`), 而不是套在聚合之外。
    #
    # 这个位置决定「圈定能不能省下扫描」, 是这条查询最关键的一处:
    #   套在聚合之外(旧写法) —— 三段照旧全表扫 376 万行, 等 GROUP BY 去重成每个酶一行
    #      之后, 才用半连接把不要的行丢掉。等价, 但**一行扫描都没省下**: 实测圈 swiss_prot
    #      反而略慢(10.50s -> 10.96s); 圈 trembl(98.4%) 更是白圈。
    #   推进段内(现写法) —— MySQL 改拿 `enzyme` 表当驱动表(`idx_enzyme_source_review`,
    #      1535 行, `Using index`), 再逐点回探 `idx_search_index_enzyme`(每次约 32 行),
    #      于是只读约 4.9 万行, 而不是 376 万行。EXPLAIN 已确认这个计划。
    #
    # 语义不变: 圈定谓词只依赖 `enzyme_id`(某个酶属不属于该来源), 而分数的 MAX 只依赖该酶
    # 自己的行 —— 谓词与聚合可交换, 先筛后聚合 == 先聚合后筛。多条件路径同理, 因为
    # (A ∪ B) ∩ F == (A ∩ F) ∪ (B ∩ F)。**已在整个语料上逐条比对**(EC / 物种 / 来源的
    # 集合相等, 见 _searchset_probe.py), 不是抽样。
    #
    # 实测(2GB 缓冲池, 热): 裸扫描 4.1s -> 段内圈定 0.57s; 冷态 7.9s -> 2.5s。
    # 唯一代价: 搜索集很宽时无东西可剪(如 trembl, 98.4%), 三段各多做一次半连接, 约 +8%。

    aggregated = f"""
        SELECT enzyme_id, MAX(score) AS score
        FROM (
            SELECT enzyme_id, weight * 4 AS score
            FROM search_index
            WHERE enzyme_id IS NOT NULL
              {scan_filter}
              AND field_value_hash = :exact_hash
            UNION ALL
            SELECT enzyme_id, weight * 2 AS score
            FROM search_index
            WHERE enzyme_id IS NOT NULL
              {scan_filter}
              AND field_value LIKE :prefix_value
            UNION ALL
            SELECT enzyme_id, weight AS score
            FROM search_index
            WHERE enzyme_id IS NOT NULL
              {scan_filter}
              AND field_value LIKE :contains_value
        ) t
        GROUP BY enzyme_id
    """
    # 圈定已进段内, 所以这里不再传 scope_sql: 只让 `_scoped_query` 补上
    # `COUNT(*) OVER ()`(total 必须在**筛选之后**数, 圈定进了段内它就天然是对的)。
    sql = _scoped_query(aggregated, "", limit, offset, with_total)

    escaped = _escape_like(value)
    bind_params = {
        **field_params,
        **scope_params,
        "exact_hash": _index_hash(value),
        "prefix_value": f"{escaped}%",
        "contains_value": f"%{escaped}%",
    }
    result = await db.execute(sa_text(sql), bind_params)
    rows = result.all()
    return await _scored(db, aggregated, "", bind_params, rows, offset, with_total)


async def _search_single_legacy(
    cond: SearchCondition,
    input_type: Optional[str],
    limit: int,
    offset: int,
    db: AsyncSession,
    value: str,
    scope: Optional[ScopeFilters] = None,
    with_total: bool = False,
) -> ScoredEnzymes:
    """Original normalized-table search used when search_index is unavailable.

    圈定谓词由**本函数自己**构建, 而不是从调用方收一个现成的字符串 ——
    它在这里必须带表别名(``e.enzyme_id``, 理由见 ``_scope_filter_sql``), 而索引路径
    那一份是不带的。收字符串就等于让「传对哪一份」成为调用方的责任, 那是一处
    只会在 search_index 空掉时才暴露的坑。让唯一知道别名的地方构建它。
    """
    scope_sql, scope_params = _scope_filter_sql(scope or ScopeFilters(),
                                                left="e.enzyme_id")

    if input_type and input_type in FIELD_CONFIG:
        fields_to_search = [input_type]
    elif cond.field and cond.field in FIELD_CONFIG:
        fields_to_search = [cond.field]
    else:
        fields_to_search = ALL_FIELDS

    union_parts = []
    bind_params = {}
    idx = 0

    for field in fields_to_search:
        cfg = FIELD_CONFIG[field]
        join_sql = TABLE_JOIN[cfg["table"]]
        filter_sql = TABLE_FILTER.get(cfg["table"], "")
        # 圈定谓词与索引路径同处理: 进每一段, 而不是套在聚合之外（理由见
        # `_search_single_index` 的注释）。这里三段本就各自 JOIN 了 enzyme, 所以
        # `e.enzyme_id IN (…)` 只是把已有的连接再用一次, 不会多出一次扫描。
        if scope_sql:
            filter_sql = f"{filter_sql} AND {scope_sql}"
        alias = cfg["alias"]
        col = cfg["column"]
        weight = cfg["weight"]

        # Exact match
        p = f"v{idx}"; idx += 1
        bind_params[p] = value
        union_parts.append(
            f"SELECT e.enzyme_id, {weight} AS score FROM enzyme e {join_sql} "
            f"WHERE {alias}.{col} = :{p}{filter_sql}"
        )

        # Prefix match
        p = f"v{idx}"; idx += 1
        bind_params[p] = f"{value}%"
        union_parts.append(
            f"SELECT e.enzyme_id, {weight // 2} AS score FROM enzyme e {join_sql} "
            f"WHERE {alias}.{col} LIKE :{p}{filter_sql}"
        )

        # Substring match (only for text fields)
        if field in ("primary_name", "gene_name", "organism", "species", "compound_name", "compound", "smiles", "formula"):
            p = f"v{idx}"; idx += 1
            bind_params[p] = f"%{value}%"
            union_parts.append(
                f"SELECT e.enzyme_id, {weight // 4} AS score FROM enzyme e {join_sql} "
                f"WHERE {alias}.{col} LIKE :{p}{filter_sql}"
            )

    if not union_parts:
        return ScoredEnzymes([], 0)

    # 圈定谓词已进每一段（见上）, 这里不再传 scope_sql —— 分页与 total 都切在
    # 过滤后的集合上, 但省下的是**扫描**：不需要的行根本不会进 UNION。
    aggregated = (
        "SELECT enzyme_id, MAX(score) AS score FROM ("
        + " UNION ALL ".join(union_parts)
        + ") t GROUP BY enzyme_id"
    )
    sql = _scoped_query(aggregated, "", limit, offset, with_total)

    all_params = {**bind_params, **(scope_params or {})}
    result = await db.execute(sa_text(sql), all_params)
    rows = result.all()
    return await _scored(db, aggregated, "", all_params, rows, offset, with_total)


async def _search_index_ready(db: AsyncSession) -> bool:
    try:
        result = await db.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = 'search_index'"
            )
        )
        if not result.scalar():
            return False
        # 这里只回答「索引非空吗」——`SELECT COUNT(*)` 会真去数完 376 万行
        # (实测 4.605s), 而本函数是**每条检索条件**都调一次, 于是 35.6s 的
        # /search/entries 里有相当一部分是在反复数同一张表。LIMIT 1 命中即返回,
        # 非空 ⇔ 取得到行, 两者对这里的布尔判定完全等价。
        row_count = await db.execute(text("SELECT 1 FROM search_index LIMIT 1"))
        return row_count.scalar() is not None
    except (ProgrammingError, OperationalError):
        await db.rollback()
        return False


async def _search_multi(
    clauses: List[SearchClause],
    input_type: Optional[str],
    limit: int,
    offset: int,
    db: AsyncSession,
    scope: Optional[ScopeFilters] = None,
) -> ScoredEnzymes:
    """OR-of-ANDs search: merge OR groups, intersect AND groups.

    ⚠️ **圈定谓词必须推进每一次 ``_search_single`` 调用, 不能只在这里过滤最终结果。**
    否则截断仍发生在**未筛选**的域上, 筛出来的还是偏的 —— 那正是本轮要消灭的
    「先截断后筛选」。这一步是**可证等价**的, 且严格更准:

        (A ∪ B) ∩ F = (A ∩ F) ∪ (B ∩ F)
        (A ∩ B) ∩ F = (A ∩ F) ∩ (B ∩ F)

    别把它「优化」回「合并之后再筛」—— 那会让筛选重新退化成对任意切片做筛选。

    ``total`` = ``len(merged)``。在每个条件取满 ``MULTI_CONDITION_CAP`` 之后这就是
    精确值(全库总共才 95,869 个酶), 所以 total 保持 ``int``, 不需要让前端多一个分支。

    返回顺序按 ``(-score, enzyme_id)`` 排定。原来这里用的是一批 Python ``set``
    的迭代顺序, 于是「看到哪 N 行」是不确定的 —— 每点一次筛选, 前 2000 行都可能换一批。
    分数在合并时取各条件中的**最高分**, 与单条件路径的 ``MAX(score)`` 同一口径
    (单条件路径本来就 ``ORDER BY score DESC``, 这里原来恒为 0 是条捷径, 不是设计)。
    """
    scope = scope or ScopeFilters()
    merged: Dict[str, int] = {}

    for clause in clauses:
        and_scores: Optional[Dict[str, int]] = None
        for cond in clause.conditions:
            found = await _search_single(
                cond, input_type, MULTI_CONDITION_CAP, 0, db, scope
            )
            scores = {eid: score for eid, score in found.rows}
            if and_scores is None:
                and_scores = scores
            else:
                and_scores = {
                    eid: max(and_scores[eid], scores[eid])
                    for eid in and_scores.keys() & scores.keys()
                }
            if not and_scores:
                break

        if and_scores:
            for eid, score in and_scores.items():
                if score > merged.get(eid, -1):
                    merged[eid] = score

    ordered = sorted(merged.items(), key=lambda item: (-item[1], item[0]))
    return ScoredEnzymes(ordered[offset:offset + limit], len(merged))


async def _fetch_cards(
    db: AsyncSession,
    enzyme_ids: List[str],
    organism_name: Optional[str] = None,
) -> List[EnzymeCard]:
    """每个 enzyme_id 一张卡; 反应/EC 取该酶的第一条边, 没有边就留空。

    来源与审核状态取自 ``enzyme`` 表**自身**的列, 不取自边 —— 全量 TrEMBL 下绝大多数酶
    (实测沙箱 4790 个里 4666 个) 没有反应边, 若从边取, 这些酶的卡片会退回硬编码的
    ``swiss_prot`` / ``official``, 把 TrEMBL 条目显示成 Swiss-Prot。
    来源/状态的**筛选**也不在这里做, 已在 ``_search_single`` 里编进取数 SQL
    (见 ``_scope_filter_sql``), 所以这里拿到的 id 本来就在目标范围内。
    """
    if not enzyme_ids:
        return []

    result = await db.execute(
        select(Enzyme).where(Enzyme.enzyme_id.in_(enzyme_ids))
    )
    enzymes = {e.enzyme_id: e for e in result.scalars().all()}
    gene_names = await _load_gene_names(db, enzyme_ids)

    cards = []
    for eid in enzyme_ids:
        enz = enzymes.get(eid)
        if not enz:
            continue

        if organism_name and enz.organism_name and organism_name.lower() not in enz.organism_name.lower():
            continue

        edge_query = (
            select(EnzymeReactionEdge, Reaction)
            .join(Reaction, EnzymeReactionEdge.reaction_id == Reaction.reaction_id)
            .where(EnzymeReactionEdge.enzyme_id == eid)
        )
        edge_result = await db.execute(edge_query.limit(1))
        row = edge_result.first()
        edge, react = row if row else (None, None)

        cards.append(EnzymeCard(
            edge_id=edge.edge_id if edge else "",
            enzyme_id=enz.enzyme_id,
            primary_name=enz.primary_name,
            uniprot_id=enz.uniprot_id,
            database_code=enz.enzyme_id,
            organism_name=enz.organism_name,
            gene_name=gene_names.get(eid),
            ec_number=react.ec_number if react else None,
            reaction_id=edge.reaction_id if edge else "",
            reaction_equation=react.equation if react else "",
            reaction_direction=react.direction.value if react and react.direction else "unknown",
            source_type=_enum_value(enz.source_type) if enz.source_type else "swiss_prot",
            review_status=_enum_value(enz.review_status) if enz.review_status else "official",
        ))

    return cards


async def _load_gene_names(db: AsyncSession, enzyme_ids: List[str]) -> Dict[str, Optional[str]]:
    if not enzyme_ids:
        return {}

    result = await db.execute(
        select(Gene)
        .where(Gene.enzyme_id.in_(enzyme_ids))
        .order_by(Gene.enzyme_id, Gene.gene_id)
    )
    gene_names: Dict[str, Optional[str]] = {}
    for gene in result.scalars().all():
        gene_names.setdefault(gene.enzyme_id, gene.gene_name)
    return gene_names
