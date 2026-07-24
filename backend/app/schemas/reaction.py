from typing import Optional, List

from app.schemas.common import CamelModel
from app.schemas.compound import CompoundCard


class ReactionDetail(CamelModel):
    reaction_id: str
    rhea_id: Optional[str] = None
    rhea_url: Optional[str] = None
    equation: str
    direction: str
    ec_number: Optional[str] = None
    smiles: Optional[str] = None
    atom_map_image_url: Optional[str] = None
    substrates: List[CompoundCard] = []
    products: List[CompoundCard] = []
    source_type: str = "swiss_prot"
    review_status: str = "official"
