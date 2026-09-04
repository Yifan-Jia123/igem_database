"""
Entry search service: multi-field weighted UNION search with AND/OR/NOT support.
"""

import re
from typing import List, Optional, Tuple, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, select
from sqlalchemy.sql import text as sa_text
from sqlalchemy.exc import ProgrammingError, OperationalError

from app.models import Enzyme, Gene, Reaction, EnzymeReactionEdge
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

    value = _normalized_search_value(cond, input_type)
    if value.upper() in EXCLUDED_COMMON_COMPOUND_IDS:
        return []

    if await _search_index_ready(db):
        indexed_results = await _search_single_index(cond, input_type, limit, offset, db, value)
        if indexed_results:
            return indexed_results

    return await _search_single_legacy(cond, input_type, limit, offset, db, value)


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


async def _search_single_index(
    cond: SearchCondition,
    input_type: Optional[str],
    limit: int,
    offset: int,
    db: AsyncSession,
    value: str,
) -> List[Tuple[str, int]]:
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

    sql = f"""
        SELECT enzyme_id, MAX(score) AS score
        FROM (
            SELECT enzyme_id, weight * 4 AS score
            FROM search_index
            WHERE enzyme_id IS NOT NULL
              {field_filter}
              AND LOWER(field_value) = LOWER(:exact_value)
            UNION ALL
            SELECT enzyme_id, weight * 2 AS score
            FROM search_index
            WHERE enzyme_id IS NOT NULL
              {field_filter}
              AND LOWER(field_value) LIKE LOWER(:prefix_value)
            UNION ALL
            SELECT enzyme_id, weight AS score
            FROM search_index
            WHERE enzyme_id IS NOT NULL
              {field_filter}
              AND LOWER(field_value) LIKE LOWER(:contains_value)
        ) t
        GROUP BY enzyme_id
        ORDER BY score DESC
        LIMIT {limit} OFFSET {offset}
    """

    bind_params = {
        **field_params,
        "exact_value": value,
        "prefix_value": f"{value}%",
        "contains_value": f"%{value}%",
    }
    result = await db.execute(sa_text(sql), bind_params)
    return [(row[0], row[1]) for row in result.all()]


async def _search_single_legacy(
    cond: SearchCondition,
    input_type: Optional[str],
    limit: int,
    offset: int,
    db: AsyncSession,
    value: str,
) -> List[Tuple[str, int]]:
    """Original normalized-table search used when search_index is unavailable."""

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
        row_count = await db.execute(text("SELECT COUNT(*) FROM search_index"))
        return bool(row_count.scalar())
    except (ProgrammingError, OperationalError):
        await db.rollback()
        return False


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
        if source_types:
            edge_query = edge_query.where(EnzymeReactionEdge.source_type.in_(source_types))
        if review_statuses:
            edge_query = edge_query.where(EnzymeReactionEdge.review_status.in_(review_statuses))
        edge_result = await db.execute(edge_query.limit(1))
        row = edge_result.first()
        edge, react = row if row else (None, None)
        if (source_types or review_statuses) and not edge:
            continue

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
            source_type=edge.source_type.value if edge and edge.source_type else "swiss_prot",
            review_status=edge.review_status.value if edge and edge.review_status else "official",
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
