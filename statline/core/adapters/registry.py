"""Canonical, process-wide adapter registry functions."""

from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import TypeVar

from statline.core.adapters.compile import compile_adapter
from statline.core.adapters.load import load_spec
from statline.core.adapters.paths import iter_adapter_paths, normalize_adapter_name
from statline.core.types.adapters import AdapterSpec, CompiledAdapter

_T = TypeVar("_T")

_compiled_cache: dict[str, CompiledAdapter] = {}
_spec_cache: dict[str, AdapterSpec] = {}
_source_cache: dict[str, Path] = {}
_generation = 0
_LOCK = RLock()


def _register_name(
    found: dict[str, _T],
    name: object,
    value: _T,
    *,
    source: str,
) -> None:
    normalized = normalize_adapter_name(name)
    if not normalized:
        return
    existing = found.get(normalized)
    if existing is not None and existing is not value:
        existing_key = getattr(existing, "key", None)
        if existing_key is None:
            metadata = getattr(existing, "metadata", None)
            existing_key = getattr(metadata, "id", "unknown")
        raise ValueError(f"Adapter name '{name}' from {source} collides with '{existing_key}'")
    found[normalized] = value


def _build_registry() -> tuple[
    dict[str, CompiledAdapter],
    dict[str, AdapterSpec],
    dict[str, Path],
]:
    compiled: dict[str, CompiledAdapter] = {}
    specs: dict[str, AdapterSpec] = {}
    sources: dict[str, Path] = {}

    for path in iter_adapter_paths():
        resolved = path.resolve()
        spec = load_spec(resolved)
        adapter = compile_adapter(spec)
        if not normalize_adapter_name(adapter.key):
            raise ValueError(f"Adapter in {path} has an empty key")

        names = (adapter.key, path.stem, *adapter.aliases)
        for name in names:
            _register_name(compiled, name, adapter, source=str(path))
            _register_name(specs, name, spec, source=str(path))
            normalized = normalize_adapter_name(name)
            if normalized:
                existing_source = sources.get(normalized)
                if existing_source is not None and existing_source != resolved:
                    raise ValueError(
                        f"Adapter name '{name}' from {path} collides with source '{existing_source}'"
                    )
                sources[normalized] = resolved

    return compiled, specs, sources


def _discover(*, force: bool = False) -> None:
    global _compiled_cache, _spec_cache, _source_cache, _generation
    with _LOCK:
        if _compiled_cache and not force:
            return
        compiled, specs, sources = _build_registry()
        # Replace the registry atomically only after every adapter loaded and compiled.
        _compiled_cache = compiled
        _spec_cache = specs
        _source_cache = sources
        _generation += 1


def _normalized_or_error(name: str) -> str:
    normalized = normalize_adapter_name(name)
    if not normalized:
        raise ValueError("adapter name is required")
    return normalized


def _unknown_adapter(name: str) -> ValueError:
    available = ", ".join(list_adapters())
    suffix = f" Available: {available}" if available else " No adapters were discovered."
    return ValueError(f"Unknown adapter '{name}'.{suffix}")


def list_adapters() -> list[str]:
    _discover()
    return sorted({adapter.key for adapter in _compiled_cache.values()})


def load_adapter(name: str) -> CompiledAdapter:
    """Return the one cached compiled adapter instance for ``name``."""
    _discover()
    normalized = _normalized_or_error(name)
    try:
        return _compiled_cache[normalized]
    except KeyError as error:
        raise _unknown_adapter(name) from error


def load_adapter_spec(name: str) -> AdapterSpec:
    """Return the parsed spec paired with the cached compiled adapter."""
    _discover()
    normalized = _normalized_or_error(name)
    try:
        return _spec_cache[normalized]
    except KeyError as error:
        raise _unknown_adapter(name) from error


def adapter_source(name: str) -> Path:
    """Return the packaged YAML source backing a cached adapter."""
    _discover()
    normalized = _normalized_or_error(name)
    try:
        return _source_cache[normalized]
    except KeyError as error:
        raise _unknown_adapter(name) from error


def refresh_adapters() -> None:
    """Atomically reload and recompile every packaged adapter."""
    _discover(force=True)


def adapter_cache_info() -> dict[str, int]:
    """Return small, JSON-safe process-cache diagnostics."""
    _discover()
    with _LOCK:
        return {
            "generation": _generation,
            "adapters": len({adapter.key for adapter in _compiled_cache.values()}),
            "names": len(_compiled_cache),
            "specs": len({id(spec) for spec in _spec_cache.values()}),
            "compiled": len({id(adapter) for adapter in _compiled_cache.values()}),
        }


def supported_adapters() -> dict[str, str]:
    _discover()
    return dict(sorted((name, adapter.key) for name, adapter in _compiled_cache.items()))


__all__ = [
    "adapter_cache_info",
    "adapter_source",
    "list_adapters",
    "load_adapter",
    "load_adapter_spec",
    "refresh_adapters",
    "supported_adapters",
]
