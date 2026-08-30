"""Gateway error definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class SlapiError(Exception):
    message: str
    detail: object | None = None

    def __post_init__(self) -> None:
        self.args = (self.message,)

    def __str__(self) -> str:
        return self.message


@dataclass
class BadRequest(SlapiError):
    pass


@dataclass
class NotFound(SlapiError):
    pass


@dataclass
class Conflict(SlapiError):
    pass


@dataclass
class Unauthorized(SlapiError):
    pass


@dataclass
class Forbidden(SlapiError):
    pass


@dataclass
class InternalError(SlapiError):
    pass


class HTTPExceptionLike(Protocol):
    status_code: int
    detail: Any


__all__ = [
    "BadRequest",
    "Conflict",
    "Forbidden",
    "HTTPExceptionLike",
    "InternalError",
    "NotFound",
    "SlapiError",
    "Unauthorized",
]
