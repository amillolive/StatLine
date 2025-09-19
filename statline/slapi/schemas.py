# statline/slapi/schemas.py  (add these near your other pydantic models)
from __future__ import annotations

from typing import Any, ClassVar, Dict, Mapping, Optional, Sequence, Union

from pydantic import BaseModel, ConfigDict

Row = Mapping[str, Any]
Rows = Sequence[Row]
Weights = Dict[str, float]
Caps = Dict[str, float]
Context = Dict[str, Dict[str, float]]

class MapRowIn(BaseModel):
    model_config: ClassVar[dict[str, Any]] = ConfigDict(extra="forbid")
    adapter: str
    row: Row

class MapBatchIn(BaseModel):
    model_config: ClassVar[dict[str, Any]] = ConfigDict(extra="forbid")
    adapter: str
    rows: Rows

class ScoreRowIn(BaseModel):
    model_config: ClassVar[dict[str, Any]] = ConfigDict(extra="forbid")
    adapter: str
    row: Row
    weights_override: Optional[Union[Weights, str]] = None
    context: Optional[Context] = None
    caps_override: Optional[Caps] = None

class ScoreBatchIn(BaseModel):
    model_config: ClassVar[dict[str, Any]] = ConfigDict(extra="forbid")
    adapter: str
    rows: Rows
    weights_override: Optional[Union[Weights, str]] = None
    context: Optional[Context] = None
    caps_override: Optional[Caps] = None

class PriSingleIn(BaseModel):
    model_config: ClassVar[dict[str, Any]] = ConfigDict(extra="forbid")
    adapter: str
    row: Row
    weights_override: Optional[Union[Weights, str]] = None

class PriBatchIn(BaseModel):
    model_config: ClassVar[dict[str, Any]] = ConfigDict(extra="forbid")
    adapter: str
    rows: Rows
    weights_override: Optional[Union[Weights, str]] = None
    context: Optional[Context] = None
    caps_mode: str = "batch"  # "batch" | "clamps"
