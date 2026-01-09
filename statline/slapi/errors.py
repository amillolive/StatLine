# statline/slapi/errors.py
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional, Union

if TYPE_CHECKING:
    # Only for typing; safe even if fastapi isn't installed thanks to mypy overrides
    from fastapi import HTTPException as FastAPIHTTPException


# ──────────────────────────────────────────────────────────────────────────────
# Base error types
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class SlapiError(Exception):
    """Base class for SLAPI errors."""
    message: str
    detail: Optional[object] = None

    def __post_init__(self) -> None:
        self.args = (self.message,)

    def __str__(self) -> str:  # pragma: no cover (trivial)
        return self.message


# 4xx – client errors
@dataclass
class BadRequest(SlapiError):
    """Input payload is syntactically valid JSON but semantically invalid."""


@dataclass
class NotFound(SlapiError):
    """Requested resource / adapter doesn’t exist."""


@dataclass
class Conflict(SlapiError):
    """Request conflicts with current state."""


@dataclass
class Unauthorized(SlapiError):
    """Missing/invalid credentials."""


@dataclass
class Forbidden(SlapiError):
    """Authenticated but not allowed."""


# 5xx – server errors
@dataclass
class InternalError(SlapiError):
    """Unexpected server-side failure."""


# ──────────────────────────────────────────────────────────────────────────────
# Mappers
# ──────────────────────────────────────────────────────────────────────────────

_STATUS_MAP: dict[type[SlapiError], int] = {
    BadRequest: 400,
    Unauthorized: 401,
    Forbidden: 403,
    NotFound: 404,
    Conflict: 409,
    InternalError: 500,
    SlapiError: 500,  # default for unknown subclass
}


def _looks_like_http_exception(err: Exception) -> bool:
    if not (hasattr(err, "status_code") and hasattr(err, "detail")):
        return False
    try:
        sc = int(getattr(err, "status_code"))
    except Exception:
        return False
    return 100 <= sc <= 599


def to_http_status(err: Exception) -> tuple[int, str]:
    """
    Convert an exception to (status_code, message) without requiring FastAPI.
    Unknown exceptions map to 500.
    """
    # Pass through HTTPException-like errors (FastAPI/Starlette) if they bubble up.
    if _looks_like_http_exception(err):
        try:
            status = int(getattr(err, "status_code"))
        except Exception:
            status = 500
        detail = getattr(err, "detail", None)
        msg = str(detail) if detail is not None else (str(err) or err.__class__.__name__)
        return status, msg

    if isinstance(err, SlapiError):
        # Find the first matching class in MRO present in the map
        for cls in type(err).mro():
            if cls in _STATUS_MAP:
                return _STATUS_MAP[cls], err.message
        return 500, err.message

    # Common Python errors → BadRequest
    if isinstance(err, (KeyError, ValueError, TypeError)):
        if isinstance(err, SlapiError):
            return 400, str(err) or err.__class__.__name__
        else:
            return 500, str(err)

    # Common permission-ish errors
    if isinstance(err, PermissionError):
        return 403, str(err) or "Forbidden"
    if isinstance(err, FileNotFoundError):
        return 404, str(err) or "Not Found"

    # Everything else → InternalError
    return 500, str(err) or "Internal Server Error"


def to_http_exception(err: Exception) -> Union[tuple[int, Any], "FastAPIHTTPException"]:
    """
    If FastAPI is available, convert to fastapi.HTTPException.
    Otherwise, return (status, detail) so callers can decide.

    Note: If `err` is already an HTTPException-like object, it is returned unchanged
    when FastAPI is installed, or mapped to (status, detail) when it isn't.
    """
    status, msg = to_http_status(err)

    # Prefer structured detail when we have it.
    detail: Any
    if isinstance(err, SlapiError) and err.detail is not None:
        detail = {"message": err.message, "detail": err.detail}
    else:
        detail = msg

    try:
        from fastapi import HTTPException as _HTTPException  # runtime import
    except Exception:  # pragma: no cover
        return status, detail

    # If it's already an HTTPException-like object, keep it.
    if _looks_like_http_exception(err):
        try:
            if isinstance(err, _HTTPException):
                return err
        except Exception:
            pass

    return _HTTPException(status_code=status, detail=detail)


__all__ = [
    "SlapiError",
    "BadRequest",
    "NotFound",
    "Conflict",
    "Unauthorized",
    "Forbidden",
    "InternalError",
    "to_http_status",
    "to_http_exception",
]
