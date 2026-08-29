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
    auth_mode: str = "api_key"
    device_verified: bool = False


__all__ = ["Principal"]
