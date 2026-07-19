"""Gateway authentication definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Set


@dataclass(frozen=True)
class Principal:
    org: str
    subject: str
    device_id: str
    api_prefix: str
    scopes: Set[str]


__all__ = ["Principal"]
