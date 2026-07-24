from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime

from app.database import Base
from app.models._enums import SourceType, ReviewStatus


class EnzymeReactionEdge(Base):
    __tablename__ = "enzyme_reaction_edge"

    edge_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    enzyme_id: Mapped[str] = mapped_column(String(20), ForeignKey("enzyme.enzyme_id"), nullable=False)
    reaction_id: Mapped[str] = mapped_column(String(30), ForeignKey("reaction.reaction_id"), nullable=False)
    source_type: Mapped[SourceType] = mapped_column("source_type", default=SourceType.swiss_prot)
    review_status: Mapped[ReviewStatus] = mapped_column("review_status", default=ReviewStatus.official)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    enzyme = relationship("Enzyme", back_populates="edges")
    reaction = relationship("Reaction", back_populates="edges")
