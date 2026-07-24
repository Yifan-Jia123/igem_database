from datetime import datetime
from typing import Optional

from app.schemas.common import CamelModel


class CompoundCard(CamelModel):
    compound_id: str
    name: str
    chebi_id: Optional[str] = None
    formula: Optional[str] = None
    charge: Optional[float] = None
    average_mass: Optional[float] = None
    smiles: Optional[str] = None
    inchi: Optional[str] = None
    structure_image_url: Optional[str] = None
    chebi_url: Optional[str] = None
    description: Optional[str] = None


CompoundNode = CompoundCard
