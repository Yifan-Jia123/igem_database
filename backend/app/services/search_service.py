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

from app.models import Enzyme, Gene, Reaction, EnzymeReactionEdge, EnzymeSolubilityScore
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

    # ⚠️ Deliberately **not** using ``scored.total``: the total here is the
    # "row count of this page", consumed by ``Pagination.total`` /
    # ``total_pages`` -- changing it would move the existing behaviour of both
    # pagination display and graph scope selection, which is unrelated to the
    # "table page display" this change targets, so don't mix it in.
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

    ``source_types`` is the **search set**: it narrows the **search scope** itself,
    compiled into the fetch SQL by ``_scoped_query``.
    ``display_*`` are the three **display filters** in the result-page toolbar
    (source / organism / EC prefix) -- they too are compiled into the fetch SQL,
    because **these three filters must be part of the same SQL**: truncate first
    by ``ORDER BY score DESC LIMIT n`` and filter client-side, and what you get is
    "those among the top n by score that happen to be of that source", unrelated
    to the real answer. Measured, `terpene synthase` at limit=600 leaves only
    **2** swiss_prot rows after client-side filtering, limit=2000 leaves **3**,
    whereas the server-side scoping gives **1,243**.
    The direction even flips with the query (`synthase` keeps 98.5% swiss_prot),
    because score is unrelated to the "source" dimension -- so "raise the cap"
    only shrinks the bias, never removes it.

    The returned ``total`` is the **true hit count before LIMIT**
    (``COUNT(*) OVER ()``), a different thing from ``len(cards)`` -- the latter is
    truncated by ``limit``. If the hit set still exceeds ``limit`` after
    filtering, the page shows a form like "2000 of 55712", which is the honest
    reading.

    ⚠️ ``display_*`` and ``source_types`` **must stay two parameter sets**: the
    former belongs only to the search results page, the latter persists across
    pages in the App and is also honoured by BLAST / the graph. Mixing them
    replays the "omitted argument at the call site -> predicate degrades to an
    empty string" incident (see below).
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

    # Seed first with the enzyme's **own** source_type. **Do not derive the
    # source from reaction edges alone** -- enzymes with no reaction annotation
    # (the great majority under full TrEMBL) have an empty edge set, so the
    # derived source_types would be [], while the search page's source filter
    # matches on `row.sourceTypes.some(...)` (SearchResultsPage.tsx:363), so
    # "picking any source hides the whole batch of edgeless enzymes": it looks
    # like other sources were filtered out, but really the full domain was
    # swapped for the subset that has edges (measured: only 124 of 4790 enzymes
    # have edges). Edge sources are merged in as before, so the docstring's
    # promise to "cover every edge" is unaffected -- measured 0 disagreements
    # (edge sources are read from the enzyme table by the ETL anyway).
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

    # Model reference score + membrane tri-state. `isoform_id IS NULL` picks the
    # row for the canonical sequence -- without this predicate an enzyme with
    # variants returns several rows at once, and there is no telling which is
    # the score for the enzyme itself.
    score_by_enzyme: Dict[str, Tuple[Optional[float], Optional[str]]] = {}
    sol_result = await db.execute(
        select(
            EnzymeSolubilityScore.enzyme_id,
            EnzymeSolubilityScore.deep_solnet_score,
            EnzymeSolubilityScore.membrane,
        ).where(
            EnzymeSolubilityScore.enzyme_id.in_(enzyme_ids),
            EnzymeSolubilityScore.isoform_id.is_(None),
        )
    )
    for row in sol_result.all():
        score_by_enzyme[row.enzyme_id] = (
            float(row.deep_solnet_score) if row.deep_solnet_score is not None else None,
            row.membrane,
        )

    cards: List[TableEnzymeCard] = []
    for eid in enzyme_ids:
        enz = enzymes.get(eid)
        if not enz:
            continue
        score, membrane = score_by_enzyme.get(eid, (None, None))
        cards.append(TableEnzymeCard(
            enzyme_id=eid,
            primary_name=enz.primary_name,
            uniprot_id=enz.uniprot_id,
            organism_name=enz.organism_name,
            gene_name=gene_names.get(eid),
            ec_numbers=sorted(ec_by_enzyme[eid], key=_ec_sort_key),
            source_types=sorted(source_by_enzyme[eid]),
            reaction_count=len(reaction_by_enzyme[eid]),
            deep_solnet_score=score,
            membrane=membrane,
        ))

    return cards


def _enum_value(value) -> str:
    """An ENUM column may come back as an enum member or a bare string."""
    return value.value if hasattr(value, "value") else str(value)


# Fetch cap for **each condition** in multi-condition search. The largest
# possible hit set is the enzyme table's row count (95,869), so this number is
# equivalent to "no truncation". It was hard-coded to 10000, so the hit set of
# a **multi-word query** like `terpene synthase` (parse_query's OR semantics
# split it into two clauses) was capped at 20,000 -- raising `limit` never
# reaches the true set, and the total counted by the window function is also a
# truncated fake. Measured cost: for the same three-stage aggregate, LIMIT 600
# is 7.896s, fetching all 37,150 ids with no row limit is 8.144s -- the cost is
# entirely in the `%word%` full scan, unrelated to the number of rows returned.
MULTI_CONDITION_CAP = 300_000

# Sentinel for the "no organism" entry in the organism filter. The frontend
# shows an empty organism_name as 'Unknown organism' and treats it as a
# selectable option, but the server matches by exact string equality, so
# "picking it filters out everything instead". The sentinel lets that entry
# translate to `organism_name IS NULL OR organism_name = ''`. The current
# database has 0 NULL rows and 0 empty-string rows, so this closes a **latent**
# defect, not a currently visible fix.
UNKNOWN_ORGANISM_SENTINEL = "__unknown__"


class ScopeFilters(NamedTuple):
    """All inputs for the **scoping predicates** compiled into SQL for one search.

    ``source_types`` / ``review_statuses`` are the **search set** (search scope);
    the other three are the search page toolbar's **display filters** (source /
    organism / EC). The two are **AND-ed** in SQL -- with search set=trembl and
    display filter=swiss_prot the result is 0 rows, which is the correct result
    of stacking the two layers, not a conflict (the frontend first renders such
    a combination as disabled and explains it in copy).

    The two **must stay two parameters**: their lifetimes and blast radius
    differ -- the search set persists across pages in the App and is also
    honoured by BLAST and the graph; the display filters belong only to the
    search results page.
    """

    source_types: Optional[List[str]] = None
    review_statuses: Optional[List[str]] = None
    display_source_types: Optional[List[str]] = None
    organism_names: Optional[List[str]] = None
    ec_prefixes: Optional[List[List[str]]] = None


class ScoredEnzymes(NamedTuple):
    """Return of one search path: this page's rows + the **true hit total**.

    ``total`` comes from ``COUNT(*) OVER ()`` (see ``_scoped_query``) and is the
    deduplicated enzyme count before LIMIT, a different thing from ``len(rows)``
    -- the latter is truncated by ``limit``. The multi-condition path's ``total``
    is ``len(merged)``, exact below ``MULTI_CONDITION_CAP``.

    ``/search/entries`` does **not** use ``total`` (its pagination semantics keep
    ``len(enzyme_ids)`` unchanged); only the table search uses it -- see
    ``search_enzyme_table``.
    """

    rows: List[Tuple[str, int]]
    total: int


def _scope_filter_sql(scope: ScopeFilters, left: str = "enzyme_id") -> Tuple[str, dict]:
    """Compile the scoping predicates (search set + display filters) into a
    **boolean condition** for the caller to wrap around its fetch query.

    Why it must go into SQL, rather than post-filtering the current page inside
    ``_fetch_cards`` like organism: with post-filtering, LIMIT/OFFSET still pages
    over the **unfiltered** full set, so "of a page of 20, 0~2 happen to be of
    that source" = the user sees a nearly blank result page, and paging also
    drops/duplicates entries; when ``select_scope_enzymes`` takes 200 candidates
    to pick the graph scope, it too is only "those of that source among the
    unfiltered first 200", silently narrowing the graph's scope. Once written
    into SQL, pagination splits the **filtered set**, matching what the user
    sees.

    This is sharper still for **table search**: the fetch cap is 2000, and the
    cost of post-filtering is not "a few rows fewer" but "how many of the 2000
    rows truncated by score happen to be Swiss-Prot" -- measured, `terpene
    synthase` at limit=600 leaves only 2 rows after client-side filtering, while
    the server-side scoping gives 1,243.

    The condition is judged on the ``enzyme`` table's **own** source_type /
    review_status -- under full TrEMBL the great majority of enzymes have no
    reaction edge, so filtering by edge would delete the whole batch of
    edgeless enzymes (the fetch domain gets swapped).

    What is returned is the **condition itself** (no WHERE), for the caller to
    splice into its own WHERE -- the index path splices it inside each of the
    three scans (see ``_search_single_index``), the legacy path likewise into
    each segment.

    ``left`` is the **table alias** of the column on the left of IN. The index
    path's ``search_index`` has only one column, ``enzyme_id``, so no table name
    is needed and there is no ambiguity; but the legacy three segments already
    JOIN in both ``enzyme`` and ``enzyme_reaction_edge`` -- **both tables have
    ``enzyme_id``**, and a bare name reports ``(1052) Column 'enzyme_id' ...
    is ambiguous``. So the legacy side must pass ``left="e.enzyme_id"``.
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

    # The search set and the "Data source" display filter are **each** an IN
    # condition, AND-ed between the two. Do not merge the two value lists into
    # one IN -- that is a union (a loosening), whereas stacking the two layers
    # wants an intersection: search set=trembl and display filter=swiss_prot
    # must yield 0 rows.
    if scope.source_types:
        conditions.append(in_condition("source_type", scope.source_types, "scope_src"))
    if scope.display_source_types:
        conditions.append(in_condition("source_type", scope.display_source_types, "disp_src"))
    if scope.review_statuses:
        conditions.append(in_condition("review_status", scope.review_statuses, "scope_rev"))

    if scope.organism_names:
        # Organism is exact string equality (frontend
        # `organismSet.has(row.organismName)`); NULL / empty string never match
        # either side -- except the sentinel entry, which specifically denotes
        # those two kinds of rows.
        known = [n for n in scope.organism_names if n != UNKNOWN_ORGANISM_SENTINEL]
        parts: List[str] = []
        if known:
            parts.append(in_condition("organism_name", known, "disp_org"))
        if len(known) != len(scope.organism_names):
            parts.append("(e.organism_name IS NULL OR e.organism_name = '')")
        conditions.append("(" + " OR ".join(parts) + ")")

    if scope.ec_prefixes:
        # EC prefixes **must** use segment-by-segment equality, not
        # `LIKE '4.2.3%'` -- the latter also matches `4.2.30.1` (treating the
        # `3` of `30` as a prefix). To use LIKE you would have to write
        # `LIKE '4.2.3.%'`, and omitting one dot silently loosens it, so
        # SUBSTRING_INDEX it is.
        #
        # The criterion is the same segment-by-segment equality as the frontend's
        # `prefixMatches` (SearchResultsPage.tsx), and it **must** go through
        # "edge ⋈ reaction" rather than the ec field in search_index -- the
        # ecNumbers on the frontend row come from edges, so an enzyme with no
        # reaction edge matches on neither side.
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
    """Wrap a "already aggregated to one row per enzyme" query with the scoping
    predicates + ordering + pagination (+ an optional true total).

    With no filters and no total, return the single-layer form **as-is** (that
    path's EXPLAIN was measured; don't introduce an extra derived table);
    otherwise add one layer: the aggregate runs first, and the semijoin runs once
    over the small deduplicated set of enzymes.

    ``with_total`` adds ``COUNT(*) OVER ()`` to the outer select. It **must be
    outer**: the window function is evaluated after GROUP BY and before LIMIT, so
    the bucket is exactly "the deduplicated enzymes" and what it counts is the
    true hit count; put it inside the aggregate select and it would count rows
    **before** aggregation. Measured, this window function is free (7.896s vs
    7.913s); the cost is decided by the `%word%` full scan.

    With ``with_total=False`` the SQL is **character-for-character identical** to
    before the change -- ``/search/entries`` takes this path, and its pagination
    semantics must stay unchanged.
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
        # Fall back to legacy only on an **empty result with no scoping
        # predicate** (the index may not cover some field). With filters, do not
        # fall back: an empty result under filters is a **normal user-visible
        # state** (the user scoped an organism with no hits), and treating it as
        # an "index doesn't cover" signal burns a more expensive full scan for
        # nothing -- measured, that is exactly the "when nothing matches, wait
        # twice as long" behaviour.
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
    """**Same function as the ETL write side**: hex of sha1(value.lower()).

    Must be character-for-character identical to etl_search_index._hash -- the
    write side computes it once and the query side again to hit
    `idx_search_index_hash`. The lower() on both sides must be Python's
    str.lower() (not MySQL's LOWER()): measured, all 137,995 rows in the
    database have a field_value_hash matching SHA1(LOWER(field_value)) exactly,
    so the two are equivalent on the current corpus.
    """
    return hashlib.sha1(value.lower().encode("utf-8")).hexdigest()


def _escape_like(value: str) -> str:
    """Escape the % and _ a user types in a LIKE into literals.

    These two are wildcards in LIKE, and `_` is common in this database's search
    values (RefSeq accessions like `NP_004453.3`) -- without escaping, an "exact
    prefix" is searched as "any single character", giving wider results that look
    like a normal fuzzy match.
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
    """Wrap one fetch's result rows into ``ScoredEnzymes``.

    With ``with_total`` the outer select has one more column, ``COUNT(*) OVER
    ()``, the deduplicated enzyme count before LIMIT. The window function only
    rides on the **rows fetched**, so an empty page has no row to attach to: an
    empty page at ``offset=0`` is equivalent to 0 hits, so return 0 directly; a
    caller with an offset (there is none currently) counts separately -- count
    honestly, so the total is not silently reported as 0.
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

    # The scoping predicate goes into each segment (reasons below); with no
    # scoping it is character-for-character identical to the original. Safety
    # premise: field_filter is either `AND field_name IN (...)` or an empty
    # string, so the concatenation is always a valid predicate sequence, never a
    # dangling AND.
    scan_filter = f"{field_filter}\n              AND {scope_sql}" if scope_sql else field_filter

    # The three UNION ALL segments differ greatly in cost; the fix targets
    # "whether the index can be used":
    #
    #   exact segment  -> `field_value_hash = :exact_hash`, using
    #              `idx_search_index_hash`. The original wrote
    #              `LOWER(field_value) = LOWER(:v)`: the column is wrapped in a
    #              function + field_value is TEXT with no index -> a full scan
    #              every time. Hash equality both uses the index and is the same
    #              algorithm as the write side. **Equivalence is measured**:
    #              distinct(field_value_hash) and distinct(LOWER(field_value))
    #              are both 34,777 across the database, and the number of groups
    #              where "one hash maps to several distinct lowercase values" is
    #              0 -- i.e. the hash is injective over lowercase values, so
    #              equality matches agree.
    #   prefix/contains segment -> drop the LOWER wrapper and let the column's own
    #              utf8mb4_unicode_ci collation do case-insensitivity (LIKE also
    #              follows the collation), so at least the index on field_name can
    #              be used, rather than running LOWER() on every row. `%...%`
    #              itself cannot use a B-tree, a known cost.
    #
    # The source/status/organism/EC filters are pushed **inside each scan**
    # (`{scan_filter}`), not wrapped outside the aggregate.
    #
    # This position decides "whether scoping saves scanning" and is the most
    # critical point of this query:
    #   outside the aggregate (old form) -- the three segments still full-scan
    #      3.76M rows, and only after GROUP BY dedups them to one row per enzyme
    #      is a semijoin used to drop the unwanted rows. Equivalent, but **not one
    #      row of scanning saved**: measured, scoping swiss_prot was actually
    #      slightly slower (10.50s -> 10.96s); scoping trembl (98.4%) is pure
    #      waste.
    #   inside the segment (current form) -- MySQL instead uses the `enzyme` table
    #      as the driving table (`idx_enzyme_source_review`, 1535 rows, `Using
    #      index`), then probes back into `idx_search_index_enzyme` per point
    #      (~32 rows each), so it reads only ~49k rows instead of 3.76M. EXPLAIN
    #      has confirmed this plan.
    #
    # Semantics unchanged: the scoping predicate depends only on `enzyme_id`
    # (whether an enzyme belongs to that source), and the MAX of the score
    # depends only on that enzyme's own rows -- the predicate and the aggregate
    # commute, filter-then-aggregate == aggregate-then-filter. The multi-condition
    # path is the same, because (A ∪ B) ∩ F == (A ∩ F) ∪ (B ∩ F). **Compared
    # row-by-row over the entire corpus** (equal sets of EC / organism / source),
    # not sampled.
    #
    # Measured (2GB buffer pool, hot): bare scan 4.1s -> in-segment scoping 0.57s;
    # cold 7.9s -> 2.5s. The only cost: when the search set is very wide there is
    # nothing to prune (e.g. trembl, 98.4%), each of the three segments does one
    # extra semijoin, about +8%.

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
    # Scoping is already inside the segments, so scope_sql is no longer passed
    # here: only let `_scoped_query` add `COUNT(*) OVER ()` (the total must be
    # counted **after** filtering, and with scoping inside the segments it is
    # naturally correct).
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

    The scoping predicate is built by **this function itself**, rather than
    receiving a ready-made string from the caller -- here it must carry the table
    alias (``e.enzyme_id``, reasons in ``_scope_filter_sql``), whereas the index
    path's copy does not. Receiving a string would make "pass the right copy" the
    caller's responsibility, a trap that only surfaces when search_index is
    empty. Let the one place that knows the alias build it.
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
        # The scoping predicate is handled as in the index path: into each
        # segment, not wrapped outside the aggregate (reasons in the
        # `_search_single_index` comment). Here the three segments already JOIN
        # enzyme individually, so `e.enzyme_id IN (…)` just reuses an existing
        # join, adding no extra scan.
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

    # The scoping predicate is already in every segment (see above), so scope_sql
    # is no longer passed here -- pagination and total both split the filtered
    # set, but what is saved is the **scanning**: unwanted rows never enter the
    # UNION at all.
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
        # This only answers "is the index non-empty" -- `SELECT COUNT(*)` really
        # counts all 3.76M rows (measured 4.605s), and this function is called
        # **per search condition**, so a fair share of the 35.6s /search/entries
        # is repeatedly counting the same table. LIMIT 1 returns as soon as it
        # hits; non-empty <=> a row is fetchable, fully equivalent for the boolean
        # test here.
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

    ⚠️ **The scoping predicate must be pushed into every ``_search_single`` call;
    it cannot just filter the final result here.** Otherwise truncation still
    happens over the **unfiltered** domain, and the filtered result is still
    biased -- exactly the "truncate then filter" this round eliminates. This step
    is **provably equivalent** and strictly more accurate:

        (A ∪ B) ∩ F = (A ∩ F) ∪ (B ∩ F)
        (A ∩ B) ∩ F = (A ∩ F) ∩ (B ∩ F)

    Don't "optimise" it back to "merge then filter" -- that would degrade
    filtering again into filtering an arbitrary slice.

    ``total`` = ``len(merged)``. Once each condition fetches up to
    ``MULTI_CONDITION_CAP``, this is exact (the whole database has only 95,869
    enzymes), so total stays ``int`` and the frontend needs no extra branch.

    The return order is set by ``(-score, enzyme_id)``. This used to follow the
    iteration order of a batch of Python ``set``s, so "which N rows you see" was
    nondeterministic -- every filter click could swap the first 2000 rows. On
    merge the score takes the **highest** among the conditions, the same criterion
    as the single-condition path's ``MAX(score)`` (the single-condition path
    already does ``ORDER BY score DESC``; here it was always 0, which was a
    shortcut, not a design).
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
    """One card per enzyme_id; reaction/EC taken from the enzyme's first edge,
    blank if it has no edge.

    Source and review status come from the ``enzyme`` table's **own** columns, not
    from an edge -- under full TrEMBL the great majority of enzymes (measured 4666
    of 4790 in the sandbox) have no reaction edge, and if taken from an edge their
    cards would fall back to the hard-coded ``swiss_prot`` / ``official``,
    displaying TrEMBL entries as Swiss-Prot. The source/status **filtering** is not
    done here either; it is already compiled into the fetch SQL in
    ``_search_single`` (see ``_scope_filter_sql``), so the ids received here are
    already within the target range.
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
