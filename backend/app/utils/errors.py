from pydantic import BaseModel
from functools import wraps
from typing import Callable

from app.schemas.common import ApiError, ErrorDetail


def register_exceptions(app):
    """Placeholder — exception handlers will be added here later."""


def error_response(code: str, message: str, details: dict | None = None) -> dict:
    return {
        "success": False,
        "error": {"code": code, "message": message, "details": details},
    }
