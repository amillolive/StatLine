# statline/core/adapters/types.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple

# ──────────────────────────────────────────────────────────────────────────────
# Typed factories (avoid mutable default pitfalls and Unknown types)
# ──────────────────────────────────────────────────────────────────────────────

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
    """
    Strict metric description consumed by the compiler/loader.

    - key: canonical metric key the adapter emits
    - source: declarative extractor (adapter-defined schema), e.g.:
        {field|ratio|sum|diff|const: ...}
      (Optional; adapters may compute in code instead.)
    - transform: optional transform: {name: ..., params: {...}}
    - clamp: optional (lo, hi) bounds applied after transform
    - bucket: optional bucket name used for scoring aggregation
    - invert: if True, lower is better (weight sign is flipped by scorer)
    """
    key: str
    source: Optional[Mapping[str, Any]] = None
    transform: Optional[Mapping[str, Any]] = None
    clamp: Optional[Tuple[float, float]] = None
    bucket: Optional[str] = None
    invert: bool = False


@dataclass(frozen=True)
class EffSpec:
    """
    Derived efficiency channel defined in adapter space (no global config).
    The scorer will compute these per-row if the adapter didn't already emit them.

    - key: resulting metric key (component scale 0..1 expected by scorer)
    - make / attempt: small expression strings resolved against the row
    - bucket: bucket name for aggregation
    - min_den: minimum denominator gate (prevents div-by-zero)
    - clamp: optional (lo, hi) on the derived value
    - invert: if True, lower is better
    - transform: optional transform applied to the derived value
    """
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
    Everything needed to map raw rows → metrics and score them lives here;
    no external/global config is required by the scorer.

    - key/version/aliases/title: identification & display
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
    buckets: Dict[str, Dict[str, Any]] = field(default_factory=_dict_str__dict_str_any)
    metrics: List[MetricSpec] = field(default_factory=_list_metrics)
    weights: Dict[str, Dict[str, float]] = field(default_factory=_dict_str__dict_str_float)
    penalties: Dict[str, Dict[str, float]] = field(default_factory=_dict_str__dict_str_float)
    efficiency: List[EffSpec] = field(default_factory=_list_eff)


__all__ = ["MetricSpec", "EffSpec", "AdapterSpec"]
