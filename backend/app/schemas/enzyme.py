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


class TableEnzymeCard(CamelModel):
    """One row per enzyme for the table search-results page.

    Unlike ``EnzymeCard`` (which is anchored to a single reaction edge), this
    aggregates every reaction edge of the matched enzyme so the UI can show and
    filter on the complete set of EC numbers and data sources it catalyses.
    """

    enzyme_id: str
    primary_name: str
    uniprot_id: Optional[str] = None
    organism_name: Optional[str] = None
    gene_name: Optional[str] = None
    ec_numbers: List[str] = []
    source_types: List[str] = []
    reaction_count: int = 0


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
    """One enzyme's full record.

    NOTE `source_type` / `review_status` here describe **the enzyme** (from the
    `enzyme` table). They are NOT the same thing as the `source_type` /
    `review_status` on each `EnzymeReactionItem` below: a reaction is a shared
    objective entity, so `etl_reactions` stamps every reaction row `swiss_prot` /
    `official` regardless of which enzyme referenced it (see that module's
    docstring). Before these two fields existed the detail page had no way to say
    whether an enzyme was reviewed, and the only "source" on screen was the
    reaction's -- which reads `swiss_prot` even for a TrEMBL enzyme. With 94,334
    of 95,869 enzymes being TrEMBL, that is the common case, not the edge case.
    """

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
    source_type: Optional[str] = None
    review_status: Optional[str] = None
    gene: Optional[GeneSummary] = None
    sequence_links: List[SequenceLink] = []
    go_terms: List[GoTerm] = []
    isoforms: List[IsoformSequence] = []
    reactions: List[EnzymeReactionItem] = []
    evidence: List[EvidenceItem] = []
    links: List[ExternalLink] = []
