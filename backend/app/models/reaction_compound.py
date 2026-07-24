from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime

from app.database import Base
from app.models._enums import CompoundRole


class ReactionCompound(Base):
    __tablename__ = "reaction_compound"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reaction_id: Mapped[str] = mapped_column(String(30), ForeignKey("reaction.reaction_id"), nullable=False)
    compound_id: Mapped[str] = mapped_column(String(30), ForeignKey("compound.compound_id"), nullable=False)
    role: Mapped[CompoundRole] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    reaction = relationship("Reaction", back_populates="reaction_compounds")
    compound = relationship("Compound")
