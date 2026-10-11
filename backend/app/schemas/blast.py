from typing import List, Literal, Optional

from app.schemas.common import CamelModel
from app.schemas.enzyme import EnzymeCard


class BlastSearchRequest(CamelModel):
    sequence: str
    e_value_threshold: float = 1e-5
    max_results: int = 100
    # Search set: BLAST actually searches, so the database is **really** built
    # from it (see blast_service._ensure_blast_db). Empty/default = whole database.
    source_types: Optional[List[str]] = None


class BlastHit(CamelModel):
    enzyme_id: str
    isoform_id: Optional[str] = None
    subject_type: Literal["canonical", "isoform"]
    subject_length: int
    e_value: float
    identity: float
    query_cover: float
    alignment_length: int
    bitscore: float
    card: Optional[EnzymeCard] = None


class BlastPayload(CamelModel):
    query_length: int
    searched_subjects: int
    threshold: float
    hits: List[BlastHit] = []
