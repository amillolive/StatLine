"""Adapter hook registry functions."""

from __future__ import annotations

from typing import Dict

from statline.core.types.adapters import AdapterHooks, NoOpHooks

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


__all__ = ["available", "clear", "get", "register"]
