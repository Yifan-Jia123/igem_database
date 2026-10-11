from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, distinct

from app.deps import get_db
from app.models import Enzyme, Reaction, Compound, SourceType, ReviewStatus, Direction, EnzymeReactionEdge
from app.schemas.common import ApiResponse

router = APIRouter()

# These modules are scoped to the **full** set of enzymes
# (table search / BLAST results table / download)
_FULL_DOMAIN_MODULES = {"table", "blast", "enzyme_table", "download"}


@router.get("/metadata/filter-options")
async def get_filter_options(
    module: str = Query(None, description="当前模块"),
    db: AsyncSession = Depends(get_db),
):
    """Allowed values for each filter. ``organisms``'s domain varies by ``module``.

    Graph / home use only enzymes with a reaction annotation (see
    ``graph_service``), so its species dropdown must be narrowed to that same
    batch of enzymes -- otherwise the dropdown would show a species that "is
    empty once the graph is picked": the full database's 96k enzymes span more
    than ten thousand species, while only a small part can enter the graph. The
    table / blast results tables are the full domain, so they keep all species.
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

    # Search-set candidates = sources that **actually have rows**, plus each count.
    # The selector must not list a 0-row set: picking it gives an empty result,
    # and the user can't tell that was their own choice. Today that's exactly
    # swiss_prot / trembl (the other two enum values have 0 rows); if
    # ai_literature data ever exists it will appear automatically, no frontend
    # change needed. The counts are also fed to the resident indicator module and
    # shown as "TrEMBL (94,334)". Hits idx_enzyme_source_review's leading column
    # (source_type, review_status), index-covered.
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
