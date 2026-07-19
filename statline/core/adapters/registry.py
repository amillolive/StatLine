"""Canonical adapter registry functions."""

from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Dict, List

from statline.core.adapters.compile import compile_adapter
from statline.core.adapters.load import load_spec
from statline.core.types.adapters import CompiledAdapter

_cache: Dict[str, CompiledAdapter] = {}
_LOCK = RLock()


def _normalize_name(value: object) -> str:
    return str(value or "").strip().casefold()


def _build_cache() -> Dict[str, CompiledAdapter]:
    base = Path(__file__).parent / "defs"
    found: Dict[str, CompiledAdapter] = {}
    for path in sorted(base.glob("*.y*ml")):
        adapter = compile_adapter(load_spec(path))
        primary = _normalize_name(adapter.key)
        if not primary:
            raise ValueError(f"Adapter in {path} has an empty key")
        if primary in found:
            raise ValueError(f"Duplicate adapter key '{adapter.key}' in {path}")
        found[primary] = adapter
        for alias in adapter.aliases:
            normalized = _normalize_name(alias)
            if not normalized:
                continue
            if normalized in found and found[normalized] is not adapter:
                raise ValueError(
                    f"Alias '{alias}' from {adapter.key} collides with another adapter"
                )
            found[normalized] = adapter
    return found


def _discover(*, force: bool = False) -> None:
    global _cache
    with _LOCK:
        if _cache and not force:
            return
        _cache = _build_cache()


def list_adapters() -> List[str]:
    _discover()
    return sorted({adapter.key for adapter in _cache.values()})


def load_adapter(name: str) -> CompiledAdapter:
    _discover()
    normalized = _normalize_name(name)
    try:
        return _cache[normalized]
    except KeyError as error:
        available = ", ".join(list_adapters())
        raise ValueError(f"Unknown adapter '{name}'. Available: {available}") from error


def refresh_adapters() -> None:
    _discover(force=True)


def supported_adapters() -> Dict[str, str]:
    _discover()
    supported: Dict[str, str] = {}
    for adapter in _cache.values():
        supported[_normalize_name(adapter.key)] = adapter.key
        supported.update({_normalize_name(alias): adapter.key for alias in adapter.aliases})
    return dict(sorted(supported.items()))


__all__ = ["list_adapters", "load_adapter", "refresh_adapters", "supported_adapters"]
