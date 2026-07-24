from typing import Optional, List

from app.schemas.common import CamelModel
from app.schemas.graph import GraphPayload


class PathwayCard(CamelModel):
    pathway_id: str
    summary: str
    compound_ids: List[str] = []
    edge_ids: List[str] = []
    edge_group_ids: List[str] = []
    step_count: int = 0
    score: Optional[float] = None
    graph: Optional[GraphPayload] = None
