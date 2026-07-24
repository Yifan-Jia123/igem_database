from typing import Optional

from app.schemas.common import CamelModel


class EvidenceItem(CamelModel):
    doi: Optional[str] = None
    pubmed_id: Optional[str] = None
    source_description: Optional[str] = None
    review_status: str = "official"
