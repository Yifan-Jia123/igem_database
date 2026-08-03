from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from typing import Optional

from app.database import Base


class GeneSequenceLink(Base):
    __tablename__ = "gene_sequence_link"

    sequence_link_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    enzyme_id: Mapped[str] = mapped_column(String(20), ForeignKey("enzyme.enzyme_id"), nullable=False)
    link_category: Mapped[str] = mapped_column(String(40), nullable=False)
    accession: Mapped[str] = mapped_column(String(80), nullable=False)
    url: Mapped[Optional[str]] = mapped_column(String(500))
    related_accession: Mapped[Optional[str]] = mapped_column(String(80))
    related_url: Mapped[Optional[str]] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    enzyme = relationship("Enzyme", back_populates="sequence_links")
