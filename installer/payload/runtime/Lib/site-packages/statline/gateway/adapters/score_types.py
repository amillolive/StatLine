"""Gateway scoring request and response definitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Union

Row = Mapping[str, Any]
Rows = List[Row]
Weights = Dict[str, float]
WeightsArg = Union[str, Weights]
Penalties = Dict[str, float]
Output = Dict[str, Any]
Filters = Dict[str, Any]
Context = Dict[str, Dict[str, float]]
Caps = Dict[str, float]
ScoreRowResponse = Dict[str, Any]
ScoreBatchResponse = List[Dict[str, Any]]


@dataclass(frozen=True)
class ScoreRowRequest:
    adapter: str
    row: Row
    weights: Optional[WeightsArg] = None
    penalties_override: Optional[Penalties] = None
    output: Optional[Output] = None
    filters: Optional[Filters] = None
    context: Optional[Context] = None
    caps_override: Optional[Caps] = None


@dataclass(frozen=True)
class ScoreBatchRequest:
    adapter: str
    rows: Rows
    weights: Optional[WeightsArg] = None
    penalties_override: Optional[Penalties] = None
    output: Optional[Output] = None
    filters: Optional[Filters] = None
    context: Optional[Context] = None
    caps_override: Optional[Caps] = None


__all__ = [
    "Caps",
    "Context",
    "Filters",
    "Output",
    "Penalties",
    "Row",
    "Rows",
    "ScoreBatchRequest",
    "ScoreBatchResponse",
    "ScoreRowRequest",
    "ScoreRowResponse",
    "Weights",
    "WeightsArg",
]
