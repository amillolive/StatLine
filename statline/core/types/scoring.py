"""Canonical scoring definitions."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class AdapterProtocol(Protocol):
    @property
    def key(self) -> str: ...

    @property
    def metrics(self) -> Sequence[Any] | Any: ...

    def map_raw(self, raw: Mapping[str, Any]) -> Mapping[str, Any]: ...


__all__ = ["AdapterProtocol"]
