from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Tuple


# typed factories (avoid Unknown from bare dict()/list())
def _dict_str__dict_str_any() -> Dict[str, Dict[str, Any]]: return {}
def _dict_str__dict_str_float() -> Dict[str, Dict[str, float]]: return {}
def _list_metrics() -> List["MetricSpec"]: return []
def _list_eff() -> List["EffSpec"]: return []

@dataclass(frozen=True)
class MetricSpec:
    """
    Strict metric description consumed by the compiler.
      - source: {field|ratio|sum|diff|const: ...}
      - transform: {name: ..., params: {...}} (optional)
      - clamp: (lo, hi) (optional)
      - bucket: optional name for bucketing in scoring
      - invert: optional flag adapters may use
    """
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
