from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeGuard, cast

from statline.gateway.http.error_types import (
    BadRequest,
    Conflict,
    Forbidden,
    HTTPExceptionLike,
    InternalError,
    NotFound,
    SlapiError,
    Unauthorized,
)

if TYPE_CHECKING:
    # Only for typing; safe even if fastapi isn't installed thanks to mypy overrides
    from fastapi import HTTPException as FastAPIHTTPException


# ──────────────────────────────────────────────────────────────────────────────
# Base error types
# ──────────────────────────────────────────────────────────────────────────────


# 4xx – client errors


# 5xx – server errors


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


def _looks_like_http_exception(
    err: object,
) -> TypeGuard[HTTPExceptionLike]:
    if not (hasattr(err, "status_code") and hasattr(err, "detail")):
        return False

    http_err = cast(HTTPExceptionLike, err)

    try:
        sc = int(http_err.status_code)
    except (TypeError, ValueError, AttributeError):
        return False

    return 100 <= sc <= 599


def to_http_status(err: Exception) -> tuple[int, str]:
    """
    Convert an exception to (status_code, message) without requiring FastAPI.
    Unknown exceptions map to 500.
    """
    # Pass through HTTPException-like errors (FastAPI/Starlette) if they bubble up.
    if _looks_like_http_exception(err):
        status = int(err.status_code)
        detail = err.detail
        msg = str(detail) if detail is not None else (str(err) or err.__class__.__name__)
        return status, msg

    if isinstance(err, SlapiError):
        # Find the first matching class in MRO present in the map
        for cls in type(err).mro():
            if cls in _STATUS_MAP:
                return _STATUS_MAP[cls], err.message
        return 500, err.message

    # Caller-shape and lookup errors are bad requests at the HTTP boundary.
    if isinstance(err, (KeyError, ValueError, TypeError)):
        return 400, str(err) or err.__class__.__name__

    # Common permission-ish errors
    if isinstance(err, PermissionError):
        return 403, str(err) or "Forbidden"
    if isinstance(err, FileNotFoundError):
        return 404, str(err) or "Not Found"

    # Everything else → InternalError
    return 500, str(err) or "Internal Server Error"


def to_http_exception(err: Exception) -> tuple[int, Any] | FastAPIHTTPException:
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
    except ImportError:  # pragma: no cover  # pragma: no cover
        return status, detail

    # If it's already an HTTPException-like object, keep it.
    if _looks_like_http_exception(err) and isinstance(err, _HTTPException):
        return err

    return _HTTPException(status_code=status, detail=detail)


__all__ = [
    "BadRequest",
    "Conflict",
    "Forbidden",
    "InternalError",
    "NotFound",
    "SlapiError",
    "Unauthorized",
    "to_http_exception",
    "to_http_status",
]
