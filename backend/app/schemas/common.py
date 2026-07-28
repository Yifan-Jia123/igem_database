from pydantic import BaseModel, ConfigDict
from typing import Any


def to_camel(s: str) -> str:
    parts = s.split("_")
    return parts[0] + "".join(p.capitalize() for p in parts[1:])


class CamelModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class Pagination(CamelModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class ApiResponse(CamelModel):
    success: bool = True
    data: Any = None
    meta: dict | None = None
    error: Any = None


class ErrorDetail(CamelModel):
    code: str
    message: str
    details: dict | None = None


class ApiError(CamelModel):
    success: bool = False
    error: ErrorDetail
