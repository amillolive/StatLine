"""Gateway storage definitions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScopeConfig:
    scope: str
    last_sync_ts: int | None


__all__ = ["ScopeConfig"]
