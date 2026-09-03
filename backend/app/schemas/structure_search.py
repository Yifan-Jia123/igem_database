from typing import List, Optional

from pydantic import Field

from app.schemas.common import CamelModel


class StructureSearchCompoundHit(CamelModel):
    compound_id: str
    name: str
    chebi_id: Optional[str] = None
    smiles: Optional[str] = None
    inchi_key: Optional[str] = None
    structure_image_url: Optional[str] = None
    chebi_url: Optional[str] = None
    description: Optional[str] = None


class StructureSearchReactionHit(CamelModel):
    reaction_id: str
    rhea_id: Optional[str] = None
    rhea_url: Optional[str] = None
    equation: str
    direction: str
    role: str
    compound_id: str
    compound_name: str
    chebi_id: Optional[str] = None
    source_type: str
    review_status: str


class StructureSearchResult(CamelModel):
    inchikey: str
    compounds: List[StructureSearchCompoundHit] = Field(default_factory=list)
    reactions: List[StructureSearchReactionHit] = Field(default_factory=list)
