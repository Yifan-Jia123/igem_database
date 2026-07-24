from typing import Optional

from app.schemas.common import CamelModel


class GeneSummary(CamelModel):
    gene_name: Optional[str] = None
    gene_id: Optional[str] = None
    genbank_id: Optional[str] = None
    ncbi_url: Optional[str] = None
    ena_accession: Optional[str] = None
    protein_accession: Optional[str] = None
