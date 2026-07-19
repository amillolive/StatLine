from __future__ import annotations

from typing import Any, ClassVar, Dict, Mapping, Optional, Sequence, Union

from pydantic import BaseModel, ConfigDict

Row = Mapping[str, Any]
Rows = Sequence[Row]

Weights = Dict[str, float]  # bucket -> weight
WeightsArg = Union[str, Weights]  # preset name OR bucket->weight override

Penalties = Dict[str, float]  # bucket -> penalty (0..1)
Output = Dict[str, Any]
Filters = Dict[str, Any]

Caps = Dict[str, float]
Context = Dict[str, Dict[str, float]]


class SniffIn(BaseModel):
    model_config: ClassVar[dict[str, Any]] = ConfigDict(extra="forbid")
    headers: Sequence[str]


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

    # v2.1 preferred:
    weights: Optional[WeightsArg] = None
    penalties_override: Optional[Penalties] = None
    output: Optional[Output] = None
    filters: Optional[Filters] = None

    # v2.0 legacy (kept for compatibility):
    weights_override: Optional[Union[Weights, str]] = None

    context: Optional[Context] = None
    caps_override: Optional[Caps] = None


class ScoreBatchIn(BaseModel):
    model_config: ClassVar[dict[str, Any]] = ConfigDict(extra="forbid")
    adapter: str
    rows: Rows

    weights: Optional[WeightsArg] = None
    penalties_override: Optional[Penalties] = None
    output: Optional[Output] = None
    filters: Optional[Filters] = None

    weights_override: Optional[Union[Weights, str]] = None

    context: Optional[Context] = None
    caps_override: Optional[Caps] = None


class PriSingleIn(BaseModel):
    model_config: ClassVar[dict[str, Any]] = ConfigDict(extra="forbid")
    adapter: str
    row: Row  # NOTE: mapped metrics for /v2/calc/pri

    weights: Optional[WeightsArg] = None
    penalties_override: Optional[Penalties] = None
    output: Optional[Output] = None

    weights_override: Optional[Union[Weights, str]] = None
    context: Optional[Context] = None
    caps_override: Optional[Caps] = None


class PriBatchIn(BaseModel):
    model_config: ClassVar[dict[str, Any]] = ConfigDict(extra="forbid")
    adapter: str
    rows: Rows  # NOTE: mapped metrics for /v2/calc/pri/batch

    weights: Optional[WeightsArg] = None
    penalties_override: Optional[Penalties] = None
    output: Optional[Output] = None

    weights_override: Optional[Union[Weights, str]] = None
    context: Optional[Context] = None
    caps_mode: str = "batch"  # "batch" | "clamps"
    caps_override: Optional[Caps] = None


class ApiKeyRequestIn(BaseModel):
    model_config: ClassVar[dict[str, Any]] = ConfigDict(extra="forbid")
    owner: Optional[str] = None
    scopes: Optional[Sequence[str]] = None
    ttl_days: Optional[int] = 30


class ApiKeyRequestDecisionIn(BaseModel):
    model_config: ClassVar[dict[str, Any]] = ConfigDict(extra="forbid")
    decided_by: str = "dev"
    note: Optional[str] = None
    scopes: Optional[Sequence[str]] = None


class EnrollIn(BaseModel):
    model_config: ClassVar[dict[str, Any]] = ConfigDict(extra="forbid")
    reg_token: str
    user: str
    email: Optional[str] = None
    device_pub_b64: str
    meta: Optional[Dict[str, Any]] = None


__all__ = [
    "ApiKeyRequestDecisionIn", "ApiKeyRequestIn", "EnrollIn", "MapBatchIn", "MapRowIn",
    "PriBatchIn", "PriSingleIn", "ScoreBatchIn", "ScoreRowIn", "SniffIn",
]
