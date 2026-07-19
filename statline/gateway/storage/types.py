"""Gateway storage definitions."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ScopeConfig:
    scope: str
    last_sync_ts: Optional[int]


__all__ = ["ScopeConfig"]
