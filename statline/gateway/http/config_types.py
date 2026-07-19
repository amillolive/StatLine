"""Gateway configuration definitions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class SlapiConfig:
    title: str = "StatLine API"
    version: str = "0.1.0"
    host: str = "127.0.0.1"
    port: int = 8000
    api_key: Optional[str] = None
    debug: bool = False


__all__ = ["SlapiConfig"]
