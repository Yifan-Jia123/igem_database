from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from typing import Optional

from app.database import Base


class EnzymeSolubilityScore(Base):
    """Per-enzyme model reference score + membrane annotation.

    Granularity: a row with `isoform_id IS NULL` is the canonical sequence's
    score, a non-NULL row is that variant's own score.

    ⚠️ **Deliberately no relationship on `Enzyme`.** The existing
    `lazy="selectin"` relationships on `Enzyme` are exactly why `_EnzymeRef`
    exists -- pulling an entity once costs a few extra SQL queries, measured at
    5.883s for the full entity vs 0.482s for a column projection (see the
    graph_service module docstring). This table is only queried **on demand,
    singly**, where a score is explicitly needed; no lazy loading.

    ⚠️ Always read canonical scores with `isoform_id IS NULL`. Without it you'd
    also pull variant rows, and one enzyme would show two scores on the graph.
    """

    __tablename__ = "enzyme_solubility_score"
    __table_args__ = (
        Index("idx_enzyme_solubility_score_enzyme", "enzyme_id"),
        Index("idx_enzyme_solubility_score_value", "deep_solnet_score"),
    )

    solubility_record_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    enzyme_id: Mapped[str] = mapped_column(String(20), ForeignKey("enzyme.enzyme_id"), nullable=False)
    isoform_id: Mapped[Optional[str]] = mapped_column(String(80))
    # DECIMAL(7,6): source data measures at most 8 chars with 6 decimals; using
    # float would lose the last digit in a round trip.
    deep_solnet_score: Mapped[Optional[float]] = mapped_column(Numeric(7, 6))
    membrane: Mapped[Optional[str]] = mapped_column(String(20))
    membrane_evidence: Mapped[Optional[str]] = mapped_column(String(1024))
    sequence_length: Mapped[Optional[int]] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
