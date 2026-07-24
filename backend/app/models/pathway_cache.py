from sqlalchemy import String, Integer, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from datetime import datetime
from typing import Optional

from app.database import Base


class PathwayCache(Base):
    __tablename__ = "pathway_cache"

    cache_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    start_compound_id: Mapped[str] = mapped_column(String(30), nullable=False)
    end_compound_id: Mapped[str] = mapped_column(String(30), nullable=False)
    via_compound_ids: Mapped[Optional[list]] = mapped_column(JSON)
    max_steps: Mapped[int] = mapped_column(Integer, default=6)
    pathway_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    hit_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
