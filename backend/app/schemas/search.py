from typing import Optional, List

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


class EntrySearchResponse(CamelModel):
    items: List[EnzymeCard] = []
    pagination: Optional[Pagination] = None
    graph_highlights: Optional[dict] = None


class PathwaySearchRequest(CamelModel):
    start_compound_id: Optional[str] = None
    end_compound_id: Optional[str] = None
    via_compound_ids: List[str] = []
    max_steps: int = 6
    source_types: Optional[List[str]] = None
    review_statuses: Optional[List[str]] = None
    limit: int = 10


class PathwaySearchResponse(CamelModel):
    items: List["PathwayCard"] = []
    graph: Optional[GraphPayload] = None


from app.schemas.pathway import PathwayCard
