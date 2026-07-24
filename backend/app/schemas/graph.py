from typing import Optional, List

from app.schemas.common import CamelModel
from app.schemas.compound import CompoundCard
from app.schemas.enzyme import EnzymeCard


class FocusPoint(CamelModel):
    node_id: Optional[str] = None
    edge_id: Optional[str] = None
    pathway_id: Optional[str] = None


class ReactionEdge(CamelModel):
    edge_id: str
    edge_group_id: Optional[str] = None
    reaction_id: str
    enzyme_id: str
    source_compound_id: str
    target_compound_id: str
    label: str
    direction: str
    source_type: str
    review_status: str
    card: Optional[EnzymeCard] = None


class EdgeGroup(CamelModel):
    edge_group_id: str
    source_compound_id: str
    target_compound_id: str
    label: str
    count: int
    edge_ids: List[str] = []


class GraphPayload(CamelModel):
    nodes: List[CompoundCard] = []
    edges: List[ReactionEdge] = []
    edge_groups: List[EdgeGroup] = []
    highlighted_node_ids: List[str] = []
    highlighted_edge_ids: List[str] = []
    focus: Optional[FocusPoint] = None
    filters: Optional[dict] = None
