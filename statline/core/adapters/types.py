# statline/core/adapters/types.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

# ──────────────────────────────────────────────────────────────────────────────
# Typed factories (avoid mutable default pitfalls and Unknown types)
# ──────────────────────────────────────────────────────────────────────────────

def _dict_str__any() -> Dict[str, Any]:
    return {}

def _dict_str__dict_str_any() -> Dict[str, Dict[str, Any]]:
    return {}

def _dict_str__dict_str_float() -> Dict[str, Dict[str, float]]:
    return {}

def _list_metrics() -> List["MetricSpec"]:
    return []

def _list_eff() -> List["EffSpec"]:
    return []


# ──────────────────────────────────────────────────────────────────────────────
# Adapter spec primitives (adapter-only; no global config, no JSON reliance)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class MetricSpec:
    key: str
    source: Optional[Mapping[str, Any]] = None
    transform: Optional[Mapping[str, Any]] = None
    clamp: Optional[Tuple[float, float]] = None
    bucket: Optional[str] = None
    invert: bool = False


@dataclass(frozen=True)
class EffSpec:
    key: str
    make: str
    attempt: str
    bucket: str
    min_den: float = 1.0
    clamp: Optional[Tuple[float, float]] = None
    invert: bool = False
    transform: Optional[Mapping[str, Any]] = None


@dataclass(frozen=True)
class AdapterSpec:
    """
    Top-level adapter specification.

    - key/version/aliases/title: identification & display
    - dimensions: optional strict enums used for filtering/rollups (metadata)
    - sniff: optional adapter selection hints (metadata)
    - filters: optional declarative filter definitions (metadata; used by SLAPI/UI)
    - buckets: metadata per bucket (free-form; scorer only needs keys)
    - metrics: list of MetricSpec that the adapter emits/understands
    - weights: named profiles → {bucket: weight}; e.g., {"pri": {...}}
    - penalties: optional named penalty profiles (adapter-defined semantics)
    - efficiency: derived channels (EffSpec) computed by the scorer if absent
    """
    key: str
    version: str
    aliases: Tuple[str, ...] = field(default_factory=tuple)
    title: str = ""

    # v2.1 metadata (preserve even if engine doesn't fully use yet)
    dimensions: Dict[str, Dict[str, Any]] = field(default_factory=_dict_str__dict_str_any)
    sniff: Dict[str, Any] = field(default_factory=_dict_str__any)
    filters: Dict[str, Dict[str, Any]] = field(default_factory=_dict_str__dict_str_any)

    buckets: Dict[str, Dict[str, Any]] = field(default_factory=_dict_str__dict_str_any)
    metrics: List[MetricSpec] = field(default_factory=_list_metrics)
    weights: Dict[str, Dict[str, float]] = field(default_factory=_dict_str__dict_str_float)
    penalties: Dict[str, Dict[str, float]] = field(default_factory=_dict_str__dict_str_float)
    efficiency: List[EffSpec] = field(default_factory=_list_eff)
    score_profiles: Dict[str, Dict[str, Any]] = field(default_factory=_dict_str__dict_str_any)


__all__ = ["MetricSpec", "EffSpec", "AdapterSpec"]
