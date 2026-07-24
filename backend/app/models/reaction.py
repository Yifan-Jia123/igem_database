from sqlalchemy import String, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from typing import Optional, List

from app.database import Base
from app.models._enums import Direction, SourceType, ReviewStatus


class Reaction(Base):
    __tablename__ = "reaction"

    reaction_id: Mapped[str] = mapped_column(String(30), primary_key=True)
    rhea_id: Mapped[Optional[str]] = mapped_column(String(20), unique=True)
    equation: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[Direction] = mapped_column(default=Direction.unknown)
    ec_number: Mapped[Optional[str]] = mapped_column(String(50))
    smiles: Mapped[Optional[str]] = mapped_column(Text)
    rhea_url: Mapped[Optional[str]] = mapped_column(String(500))
    atom_map_image_url: Mapped[Optional[str]] = mapped_column(String(500))
    source_type: Mapped[SourceType] = mapped_column("source_type", default=SourceType.swiss_prot)
    review_status: Mapped[ReviewStatus] = mapped_column("review_status", default=ReviewStatus.official)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    reaction_compounds = relationship("ReactionCompound", back_populates="reaction", lazy="selectin")
    edges = relationship("EnzymeReactionEdge", back_populates="reaction", lazy="selectin")
