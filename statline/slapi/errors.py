# statline/slapi/errors.py
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Union

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
    """Request conflicts with current state (rare for our scoring API)."""


@dataclass
class Unauthorized(SlapiError):
    """Missing/invalid credentials (reserved for future auth)."""


@dataclass
class Forbidden(SlapiError):
    """Authenticated but not allowed (reserved for future authz)."""


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


def to_http_status(err: Exception) -> tuple[int, str]:
    """
    Convert an exception to (status_code, message) without requiring FastAPI.
    Unknown exceptions map to 500.
    """
    if isinstance(err, SlapiError):
        # Find the first matching class in MRO present in the map
        for cls in type(err).mro():
            if cls in _STATUS_MAP:
                return _STATUS_MAP[cls], err.message
        return 500, err.message

    # Common Python errors → BadRequest
    if isinstance(err, (KeyError, ValueError, TypeError)):
        return 400, str(err) or err.__class__.__name__

    # Everything else → InternalError
    return 500, str(err) or "Internal Server Error"


def to_http_exception(
    err: Exception,
) -> Union[tuple[int, str], "FastAPIHTTPException"]:
    """
    If FastAPI is available, convert to fastapi.HTTPException.
    Otherwise, return (status, message) so callers can decide.
    """
    status, msg = to_http_status(err)
    try:
        from fastapi import HTTPException as _HTTPException  # runtime import
    except Exception:  # pragma: no cover
        return status, msg
    return _HTTPException(status_code=status, detail=msg)


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
