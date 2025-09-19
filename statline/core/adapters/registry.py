# statline/core/adapters/registry.py
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from .compile import CompiledAdapter, compile_adapter
from .loader import load_spec

# Cache of all compiled adapters, keyed by primary key + aliases (lowercased).
_CACHE: Dict[str, CompiledAdapter] = {}


def _discover() -> None:
    """
    Populate the adapter cache by compiling all YAML specs in defs/.
    Both primary keys and aliases are cached (lowercased).
    """
    base = Path(__file__).parent / "defs"
    found: Dict[str, CompiledAdapter] = {}

    for y in sorted(base.glob("*.y*ml")):
        spec = load_spec(y.stem)
        comp = compile_adapter(spec)

        # Primary key
        pk = comp.key.lower()
        if pk in found:
            raise ValueError(f"Duplicate adapter key '{comp.key}' in {y}")
        found[pk] = comp

        # Aliases
        for alias in comp.aliases:
            ak = alias.lower()
            if ak in found and found[ak] is not comp:
                raise ValueError(
                    f"Alias '{alias}' from {comp.key} collides with another adapter"
                )
            found[ak] = comp

    _CACHE.clear()
    _CACHE.update(found)


def list_names() -> List[str]:
    """Return sorted list of adapter primary keys (not aliases)."""
    if not _CACHE:
        _discover()
    return sorted({c.key for c in _CACHE.values()})


def load(name: str) -> CompiledAdapter:
    """
    Load an adapter by name or alias. Raises ValueError if not found.
    """
    if not _CACHE:
        _discover()
    key = (name or "").lower()
    try:
        return _CACHE[key]
    except KeyError:
        raise ValueError(
            f"Unknown adapter '{name}'. Available: {', '.join(list_names())}"
        )


def refresh() -> None:
    """Force re-scan of defs/ (useful in tests or dev)."""
    _discover()
