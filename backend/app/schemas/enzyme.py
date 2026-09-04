from typing import Optional, List

from app.schemas.common import CamelModel
from app.schemas.gene import GeneSummary, SequenceLink
from app.schemas.evidence import EvidenceItem
from app.schemas.compound import CompoundCard


class EnzymeCard(CamelModel):
    edge_id: str
    enzyme_id: str
    primary_name: str
    uniprot_id: Optional[str] = None
    database_code: str
    organism_name: Optional[str] = None
    gene_name: Optional[str] = None
    ec_number: Optional[str] = None
    reaction_id: str
    reaction_equation: str
    reaction_direction: str
    source_type: str
    review_status: str


class EnzymeReactionItem(CamelModel):
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


class ExternalLink(CamelModel):
    label: str
    url: str


class GoTerm(CamelModel):
    go_id: Optional[str] = None
    go_term: Optional[str] = None
    go_url: Optional[str] = None


class IsoformSequence(CamelModel):
    isoform_id: Optional[str] = None
    isoform_length: Optional[int] = None
    isoform_mass: Optional[str] = None
    canonical_sequence: Optional[str] = None
    canonical_length: Optional[int] = None
    canonical_mass: Optional[str] = None
    sequence: Optional[str] = None


class EnzymeDetail(CamelModel):
    enzyme_id: str
    database_code: str
    primary_name: str
    secondary_names: List[str] = []
    uniprot_id: Optional[str] = None
    uniprot_url: Optional[str] = None
    organism_name: Optional[str] = None
    sequence: Optional[str] = None
    length: Optional[int] = None
    mass: Optional[float] = None
    gene: Optional[GeneSummary] = None
    sequence_links: List[SequenceLink] = []
    go_terms: List[GoTerm] = []
    isoforms: List[IsoformSequence] = []
    reactions: List[EnzymeReactionItem] = []
    evidence: List[EvidenceItem] = []
    links: List[ExternalLink] = []
