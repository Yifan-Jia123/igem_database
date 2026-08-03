"""
Entry search service: multi-field weighted UNION search with AND/OR/NOT support.
"""

from typing import List, Optional, Tuple, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select
from sqlalchemy.sql import text as sa_text

from app.models import Enzyme, Reaction, EnzymeReactionEdge
from app.schemas.enzyme import EnzymeCard
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

    if len(clauses) == 1 and len(clauses[0].conditions) == 1:
        cond = clauses[0].conditions[0]
        enzyme_scores = await _search_single(cond, input_type, page_size, offset, db)
    else:
        enzyme_scores = await _search_multi(clauses, input_type, page_size, offset, db)

    enzyme_ids = [es[0] for es in enzyme_scores] if enzyme_scores else []

    if not enzyme_ids:
        return [], Pagination(page=page, page_size=page_size, total=0, total_pages=0), None

    cards = await _fetch_cards(db, enzyme_ids, organism_name, source_types, review_statuses)

    graph_highlights = None
    if view_mode == "graph":
        edge_ids = [c.edge_id for c in cards if c.edge_id]
        if edge_ids:
            graph_highlights = {"highlightedEdgeIds": edge_ids}

    total = len(enzyme_ids)
    total_pages = max(1, (total + page_size - 1) // page_size)

    return (
        cards,
        Pagination(page=page, page_size=page_size, total=total, total_pages=total_pages),
        graph_highlights,
    )


async def _search_single(
    cond: SearchCondition,
    input_type: Optional[str],
    limit: int,
    offset: int,
    db: AsyncSession,
) -> List[Tuple[str, int]]:
    """Search for one condition, return [(enzyme_id, score), ...]."""

    value = cond.value

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
        return []

    sql = (
        f"SELECT enzyme_id, MAX(score) AS score FROM ("
        + " UNION ALL ".join(union_parts)
        + f") t GROUP BY enzyme_id ORDER BY score DESC LIMIT {limit} OFFSET {offset}"
    )

    result = await db.execute(sa_text(sql), bind_params)
    return [(row[0], row[1]) for row in result.all()]


async def _search_multi(
    clauses: List[SearchClause],
    input_type: Optional[str],
    limit: int,
    offset: int,
    db: AsyncSession,
) -> List[Tuple[str, int]]:
    """OR-of-ANDs search: merge OR groups, intersect AND groups."""

    or_sets: List[set] = []

    for clause in clauses:
        and_ids: Optional[set] = None
        for cond in clause.conditions:
            results = await _search_single(cond, input_type, 10000, 0, db)
            ids = set(r[0] for r in results)
            if and_ids is None:
                and_ids = ids
            else:
                and_ids = and_ids & ids
            if not and_ids:
                break

        if and_ids:
            or_sets.append(and_ids)

    merged: List[str] = []
    seen = set()
    for s in or_sets:
        for eid in s:
            if eid not in seen:
                seen.add(eid)
                merged.append(eid)

    return [(eid, 0) for eid in merged[offset:offset + limit]]


async def _fetch_cards(
    db: AsyncSession,
    enzyme_ids: List[str],
    organism_name: Optional[str] = None,
    source_types: Optional[List[str]] = None,
    review_statuses: Optional[List[str]] = None,
) -> List[EnzymeCard]:
    if not enzyme_ids:
        return []

    result = await db.execute(
        select(Enzyme).where(Enzyme.enzyme_id.in_(enzyme_ids))
    )
    enzymes = {e.enzyme_id: e for e in result.scalars().all()}

    cards = []
    for eid in enzyme_ids:
        enz = enzymes.get(eid)
        if not enz:
            continue

        if organism_name and enz.organism_name and organism_name.lower() not in enz.organism_name.lower():
            continue

        edge_result = await db.execute(
            select(EnzymeReactionEdge, Reaction)
            .join(Reaction, EnzymeReactionEdge.reaction_id == Reaction.reaction_id)
            .where(EnzymeReactionEdge.enzyme_id == eid)
            .limit(1)
        )
        row = edge_result.first()
        edge, react = row if row else (None, None)

        cards.append(EnzymeCard(
            edge_id=edge.edge_id if edge else "",
            enzyme_id=enz.enzyme_id,
            primary_name=enz.primary_name,
            uniprot_id=enz.uniprot_id,
            database_code=enz.enzyme_id,
            organism_name=enz.organism_name,
            ec_number=react.ec_number if react else None,
            reaction_id=edge.reaction_id if edge else "",
            reaction_equation=react.equation if react else "",
            reaction_direction=react.direction.value if react and react.direction else "unknown",
            source_type=edge.source_type.value if edge and edge.source_type else "swiss_prot",
            review_status=edge.review_status.value if edge and edge.review_status else "official",
        ))

    return cards
