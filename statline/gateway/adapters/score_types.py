"""Gateway scoring request and response definitions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

Row = Mapping[str, Any]
Rows = list[Row]
Weights = dict[str, float]
WeightsArg = str | Weights
Penalties = dict[str, float]
Output = dict[str, Any]
Filters = dict[str, Any]
Context = dict[str, dict[str, float]]
Caps = dict[str, float]
ScoreRowResponse = dict[str, Any]
ScoreBatchResponse = list[dict[str, Any]]


@dataclass(frozen=True)
class ScoreRowRequest:
    adapter: str
    row: Row
    weights: WeightsArg | None = None
    penalties_override: Penalties | None = None
    output: Output | None = None
    profiles: Sequence[str] | None = None
    filters: Filters | None = None
    context: Context | None = None
    caps_override: Caps | None = None


@dataclass(frozen=True)
class ScoreBatchRequest:
    adapter: str
    rows: Rows
    weights: WeightsArg | None = None
    penalties_override: Penalties | None = None
    output: Output | None = None
    profiles: Sequence[str] | None = None
    filters: Filters | None = None
    context: Context | None = None
    caps_override: Caps | None = None


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
