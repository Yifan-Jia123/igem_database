from typing import Optional, List

from pydantic import Field

from app.schemas.common import CamelModel, Pagination
from app.schemas.enzyme import EnzymeCard
from app.schemas.graph import GraphPayload


class EntrySearchRequest(CamelModel):
    q: str
    input_type: Optional[str] = None
    view_mode: Optional[str] = "table"
    organism_name: Optional[str] = None
    source_types: Optional[List[str]] = None
    review_statuses: Optional[List[str]] = None
    page: int = 1
    page_size: int = 20
    sort_by: Optional[str] = None
    sort_order: Optional[str] = "asc"


class TableByIdsRequest(CamelModel):
    """Rich table rows for an explicit enzyme-id list, order preserved.

    Feeds BLAST hits (which arrive as an enzyme-id list in E-value order)
    through the same aggregated rows the keyword table page filters on.
    """

    enzyme_ids: List[str] = []


class EntrySearchResponse(CamelModel):
    items: List[EnzymeCard] = []
    pagination: Optional[Pagination] = None
    graph_highlights: Optional[dict] = None


class PathwaySearchRequest(CamelModel):
    """Pathway mode query: an ordered start → (…via…) → end compound chain.

    ``start_compound_id``/``end_compound_id``/``via_compound_ids`` accept a
    compound id, a (bare or ``CHEBI:``-prefixed) ChEBI number, or a compound
    name — each is resolved server-side to one displayable compound."""

    start_compound_id: Optional[str] = None
    end_compound_id: Optional[str] = None
    via_compound_ids: List[str] = []
    max_steps: int = Field(default=6, ge=1, le=8)
    source_types: Optional[List[str]] = None
    review_statuses: Optional[List[str]] = None
    limit: int = Field(default=10, ge=1, le=40)


class PathwaySearchResponse(CamelModel):
    items: List["PathwayCard"] = []
    total: int = 0
    graph: Optional[GraphPayload] = None


from app.schemas.pathway import PathwayCard
