from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    generation: int
    core: int
    gateway: int
    app: int
