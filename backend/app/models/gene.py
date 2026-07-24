from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from typing import Optional

from app.database import Base


class Gene(Base):
    __tablename__ = "gene"

    gene_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    enzyme_id: Mapped[str] = mapped_column(String(20), ForeignKey("enzyme.enzyme_id"), nullable=False)
    gene_name: Mapped[Optional[str]] = mapped_column(String(200))
    genbank_id: Mapped[Optional[str]] = mapped_column(String(50))
    ncbi_url: Mapped[Optional[str]] = mapped_column(String(500))
    ena_accession: Mapped[Optional[str]] = mapped_column(String(50))
    protein_accession: Mapped[Optional[str]] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    enzyme = relationship("Enzyme", back_populates="genes")
