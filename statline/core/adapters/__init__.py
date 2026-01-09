# statline/core/adapters/__init__.py
from __future__ import annotations

from typing import Dict

from .compile import CompiledAdapter
from .registry import list_names, load, refresh  # canonical YAML registry API
from .sniff import sniff_adapters

# --------------------------------------------------------------------------------------
# Canonical adapter surface
#
# StatLine v2+ uses YAML adapter specs compiled into `CompiledAdapter` objects.
# This package used to support "adapter modules" discovered via pkgutil/importlib.
# That path is intentionally removed here to avoid split-brain behavior.
# --------------------------------------------------------------------------------------

# Back-compat type name: older call sites may import Adapter from here.
Adapter = CompiledAdapter


def load_adapter(name: str) -> CompiledAdapter:
    """
    Load an adapter by primary key or alias (case-insensitive).

    This is a thin wrapper around `registry.load()` and is the canonical adapter loader.

    Example:
        adp = load_adapter("demo")
        metrics = adp.map_raw_to_metrics({"pts": 20, "ast": 5})
    """
    return load(name)


def supported_adapters() -> Dict[str, str]:
    """
    Return a mapping of adapter key/alias -> canonical primary key.

    Example return:
        {
          "demo": "demo",
          "d": "demo",
          "basketball_demo": "demo",
        }

    Notes:
    - Keys are lowercased.
    - Values are the adapter's primary key (as declared in the spec).
    """
    out: Dict[str, str] = {}
    for primary in list_names():
        adp = load(primary)
        out[adp.key.lower()] = adp.key
        for alias in adp.aliases:
            out[alias.lower()] = adp.key
    return dict(sorted(out.items()))


__all__ = [
    # Types
    "CompiledAdapter",
    "Adapter",
    # Primary API
    "list_names",
    "load",
    "refresh",
    "supported_adapters",
    "load_adapter",
    "sniff_adapters",
]
