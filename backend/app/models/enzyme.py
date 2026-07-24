from sqlalchemy import String, Text, Integer, DECIMAL, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from typing import Optional, List

from app.database import Base
from app.models._enums import SourceType, ReviewStatus


class Enzyme(Base):
    __tablename__ = "enzyme"

    enzyme_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    uniprot_id: Mapped[Optional[str]] = mapped_column(String(20), unique=True)
    primary_name: Mapped[str] = mapped_column(String(500), nullable=False)
    secondary_names: Mapped[Optional[list]] = mapped_column(JSON)
    organism_name: Mapped[Optional[str]] = mapped_column(String(300))
    sequence: Mapped[Optional[str]] = mapped_column(Text)
    length: Mapped[Optional[int]] = mapped_column(Integer)
    mass: Mapped[Optional[float]] = mapped_column(DECIMAL(12, 2))
    source_type: Mapped[SourceType] = mapped_column(
        "source_type", default=SourceType.swiss_prot
    )
    review_status: Mapped[ReviewStatus] = mapped_column(
        "review_status", default=ReviewStatus.official
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    genes = relationship("Gene", back_populates="enzyme", lazy="selectin")
    edges = relationship("EnzymeReactionEdge", back_populates="enzyme", lazy="selectin")
    evidences = relationship("Evidence", back_populates="enzyme", lazy="selectin")
