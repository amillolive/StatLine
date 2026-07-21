"""Canonical adapter registry functions."""

from __future__ import annotations

from threading import RLock
from typing import Dict, List

from statline.core.adapters.compile import compile_adapter
from statline.core.adapters.load import load_spec
from statline.core.adapters.paths import iter_adapter_paths, normalize_adapter_name
from statline.core.types.adapters import CompiledAdapter

_cache: Dict[str, CompiledAdapter] = {}
_LOCK = RLock()


def _register_name(
    found: Dict[str, CompiledAdapter],
    name: object,
    adapter: CompiledAdapter,
    *,
    source: str,
) -> None:
    normalized = normalize_adapter_name(name)
    if not normalized:
        return
    existing = found.get(normalized)
    if existing is not None and existing is not adapter:
        raise ValueError(f"Adapter name '{name}' from {source} collides with '{existing.key}'")
    found[normalized] = adapter


def _build_cache() -> Dict[str, CompiledAdapter]:
    found: Dict[str, CompiledAdapter] = {}
    for path in iter_adapter_paths():
        adapter = compile_adapter(load_spec(path))
        if not normalize_adapter_name(adapter.key):
            raise ValueError(f"Adapter in {path} has an empty key")
        _register_name(found, adapter.key, adapter, source=str(path))
        _register_name(found, path.stem, adapter, source=str(path))
        for alias in adapter.aliases:
            _register_name(found, alias, adapter, source=str(path))
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
    normalized = normalize_adapter_name(name)
    try:
        return _cache[normalized]
    except KeyError as error:
        available = ", ".join(list_adapters())
        suffix = f" Available: {available}" if available else " No adapters were discovered."
        raise ValueError(f"Unknown adapter '{name}'.{suffix}") from error


def refresh_adapters() -> None:
    _discover(force=True)


def supported_adapters() -> Dict[str, str]:
    _discover()
    return dict(sorted((name, adapter.key) for name, adapter in _cache.items()))


__all__ = ["list_adapters", "load_adapter", "refresh_adapters", "supported_adapters"]
