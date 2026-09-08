from typing import Optional, List

from app.schemas.common import CamelModel
from app.schemas.graph import GraphPayload


class PathwaySegment(CamelModel):
    """One step of a pathway: an ordered displayable compound pair plus the
    map edge that realises it — either a single enzyme ``edge_id`` or a
    collapsed composite ``edge_group_id`` (never both, never hand-built).
    Carried on every card as the extension point for the future pathway
    detail page."""

    source_compound_id: str
    target_compound_id: str
    edge_id: Optional[str] = None
    edge_group_id: Optional[str] = None


class PathwayCard(CamelModel):
    pathway_id: str
    summary: str
    compound_ids: List[str] = []
    edge_ids: List[str] = []
    edge_group_ids: List[str] = []
    segments: List[PathwaySegment] = []
    step_count: int = 0
    score: Optional[float] = None
    graph: Optional[GraphPayload] = None
