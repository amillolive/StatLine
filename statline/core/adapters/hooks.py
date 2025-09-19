# statline/core/adapters/hooks.py
from __future__ import annotations

from typing import Any, Dict, Iterable, Protocol, runtime_checkable


@runtime_checkable
class AdapterHooks(Protocol):
    """
    Optional per-adapter hook points.
    Adapters can export a hooks object matching this Protocol. All methods are optional:
    implement none, some, or all.
    """

    # Called before mapping expressions run; may mutate/augment the raw row.
    # Return the (possibly) modified row. Implementations should return a new dict or
    # the same object if unchanged.
    def pre_map(self, row: Dict[str, Any]) -> Dict[str, Any]: ...  # pragma: no cover

    # Called after mapping → metrics; may add derived metrics or fixups.
    # Should return a metrics dict containing only numeric values.
    def post_map(self, metrics: Dict[str, float]) -> Dict[str, float]: ...  # pragma: no cover

    # Optional format sniffing (e.g., CSV headers) to help pick the right adapter.
    # Return True if this hook "recognizes" the schema.
    def sniff(self, headers: Iterable[str]) -> bool: ...  # pragma: no cover


class NoOpHooks:
    """Default no-op implementation used when an adapter doesn't provide hooks."""
    def pre_map(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return row

    def post_map(self, metrics: Dict[str, float]) -> Dict[str, float]:
        return metrics

    def sniff(self, headers: Iterable[str]) -> bool:
        return False


# Simple registry for hook modules keyed by adapter key.
_HOOKS: Dict[str, AdapterHooks] = {}


def register(key: str, hooks: AdapterHooks) -> None:
    """Register a hooks object for an adapter key (case-insensitive)."""
    _HOOKS[key.lower()] = hooks


def get(key: str) -> AdapterHooks:
    """Fetch hooks for an adapter key; returns NoOpHooks() if none registered."""
    return _HOOKS.get(key.lower(), NoOpHooks())


def available() -> Dict[str, AdapterHooks]:
    """Return a shallow copy of the current registry (useful for diagnostics/tests)."""
    return dict(_HOOKS)


def clear() -> None:
    """Clear the registry (useful in tests)."""
    _HOOKS.clear()


__all__ = ["AdapterHooks", "NoOpHooks", "register", "get", "available", "clear"]
