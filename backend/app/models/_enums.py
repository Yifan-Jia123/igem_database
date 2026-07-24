from sqlalchemy import String, Text, Integer, DECIMAL, JSON, Enum, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime
from enum import Enum as PyEnum

from app.database import Base


class SourceType(str, PyEnum):
    swiss_prot = "swiss_prot"
    trembl = "trembl"
    ai_literature = "ai_literature"
    manual_literature = "manual_literature"


class ReviewStatus(str, PyEnum):
    pending = "pending"
    reviewed = "reviewed"
    official = "official"
    deprecated = "deprecated"


class Direction(str, PyEnum):
    forward = "forward"
    reverse = "reverse"
    reversible = "reversible"
    unknown = "unknown"


class CompoundRole(str, PyEnum):
    substrate = "substrate"
    product = "product"
