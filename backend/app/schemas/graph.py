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


class EdgeGroupItem(CamelModel):
    """Minimal per-sub-edge summary carried on a collapsed composite edge.

    Lets the client apply species/source-type filters and search highlights to
    the individual edges inside a composite without expanding it.
    """

    edge_id: str
    enzyme_id: str
    label: Optional[str] = None
    organism_name: Optional[str] = None
    source_type: Optional[str] = None
    review_status: Optional[str] = None
    # The client uses these two for local filtering (threshold + membrane-only),
    # so they hang on **every sub-edge**, not just on the expanded cards -- a
    # collapsed composite edge must be able to decide visibility too.
    deep_solnet_score: Optional[float] = None
    membrane: Optional[str] = None


class EdgeGroup(CamelModel):
    edge_group_id: str
    source_compound_id: str
    target_compound_id: str
    label: str
    count: int
    # The N in `label`, i.e. the number of **distinct enzymes**. `count` is the
    # record count -- the same enzyme catalysing the same compound pair via two
    # reactions produces two records (one edge_id each). In the whole database 11
    # of 175 groups differ between the two; the largest (FPP↔squalene) has
    # count=7093 with only 3595 enzymes. The UI's `enzyme*N` line wants the
    # latter, but `count` must not change with it: the client uses
    # "edgeIds.length >= count" to judge whether the payload was truncated
    # (`buildHomePairs` in `graphExperience.tsx`), and as the sort key for the
    # collage map.
    enzyme_count: int
    edge_ids: List[str] = []
    items: List[EdgeGroupItem] = []


class GraphPayload(CamelModel):
    nodes: List[CompoundCard] = []
    edges: List[ReactionEdge] = []
    edge_groups: List[EdgeGroup] = []
    highlighted_node_ids: List[str] = []
    highlighted_edge_ids: List[str] = []
    focus: Optional[FocusPoint] = None
    filters: Optional[dict] = None
