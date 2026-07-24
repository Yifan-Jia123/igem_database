from typing import Optional, List

from app.schemas.common import CamelModel
from app.schemas.enzyme import EnzymeCard


class HomologySearchRequest(CamelModel):
    enzyme_id: Optional[str] = None
    sequence: Optional[str] = None
    e_value_threshold: float = 1e-5
    max_results: int = 50
    source_types: Optional[List[str]] = None


class HomologyResultItem(CamelModel):
    enzyme_id: str
    e_value: float
    identity: Optional[float] = None
    card: Optional[EnzymeCard] = None


class HomologyJobStatus(CamelModel):
    job_id: str
    status: str
    progress: Optional[int] = None
    results: List[HomologyResultItem] = []
