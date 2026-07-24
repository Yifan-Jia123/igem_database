from sqlalchemy import String, Text, DECIMAL, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from typing import Optional

from app.database import Base


class Compound(Base):
    __tablename__ = "compound"

    compound_id: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    chebi_id: Mapped[Optional[str]] = mapped_column(String(20))
    formula: Mapped[Optional[str]] = mapped_column(String(200))
    charge: Mapped[Optional[float]] = mapped_column(DECIMAL(6, 2))
    average_mass: Mapped[Optional[float]] = mapped_column(DECIMAL(12, 4))
    smiles: Mapped[Optional[str]] = mapped_column(Text)
    inchi: Mapped[Optional[str]] = mapped_column(Text)
    structure_image_url: Mapped[Optional[str]] = mapped_column(String(500))
    chebi_url: Mapped[Optional[str]] = mapped_column(String(500))
    description: Mapped[Optional[str]] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
