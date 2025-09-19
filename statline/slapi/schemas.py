# statline/slapi/schemas.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar, Dict, Mapping, Optional, Sequence

from pydantic import (  # pyright: ignore[reportMissingModuleSource]
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
)

# ──────────────────────────────────────────────────────────────────────────────
# Common aliases (kept explicit for clarity)
# ──────────────────────────────────────────────────────────────────────────────

Weights = Dict[str, float]
Caps = Dict[str, float]
Context = Dict[str, Dict[str, float]]
Row = Mapping[str, Any]        # <-- immutable, covariant
Rows = Sequence[Row]           # <-- covariant sequence


# ──────────────────────────────────────────────────────────────────────────────
# Requests (Pydantic v2 only)
# ──────────────────────────────────────────────────────────────────────────────

class ScoreRowRequestModel(BaseModel):
    """Public API payload for scoring a single raw row via an adapter."""
    model_config: ClassVar[dict[str, Any]] = ConfigDict(extra="forbid")

    adapter: str = Field(..., description="Adapter key (e.g., 'rbw5').")
    row: Row = Field(..., description="Raw input row understood by the adapter.")
    weights_override: Optional[Weights] = Field(
        None, description="Optional bucket→weight overrides for the 'pri' profile."
    )
    context: Optional[Context] = Field(
        None, description="Optional external leaders/floors context per metric."
    )
    caps_override: Optional[Caps] = Field(
        None, description="Optional per-metric caps (bypass context/clamps)."
    )


class ScoreBatchRequestModel(BaseModel):
    """Public API payload for scoring multiple raw rows via an adapter."""
    model_config: ClassVar[dict[str, Any]] = ConfigDict(extra="forbid")

    adapter: str = Field(..., description="Adapter key (e.g., 'rbw5').")
    rows: Rows = Field(..., description="List of raw rows to score in order.")
    weights_override: Optional[Weights] = Field(
        None, description="Optional bucket→weight overrides for the 'pri' profile."
    )
    context: Optional[Context] = Field(
        None, description="Optional external leaders/floors context per metric."
    )
    caps_override: Optional[Caps] = Field(
        None, description="Optional per-metric caps (bypass context/clamps)."
    )


# ──────────────────────────────────────────────────────────────────────────────
# Responses (mirror core output shape)
# ──────────────────────────────────────────────────────────────────────────────

class ScoreRowResponseModel(BaseModel):
    """Core PRI output for a single row."""
    model_config: ClassVar[dict[str, Any]] = ConfigDict(extra="forbid")

    pri: int = Field(..., ge=0, le=99, description="Clamped PRI score (0..99).")
    pri_raw: float = Field(..., ge=0.0, le=1.0, description="Normalized raw score (0..1).")
    buckets: Dict[str, float] = Field(default_factory=dict)
    components: Dict[str, float] = Field(default_factory=dict)
    weights: Dict[str, float] = Field(default_factory=dict)
    context_used: str = Field("batch")


class ScoreBatchResponseModel(RootModel[Sequence[ScoreRowResponseModel]]):
    """List wrapper for batch responses (keeps FastAPI/OpenAPI concise)."""
    root: Sequence[ScoreRowResponseModel]


# ──────────────────────────────────────────────────────────────────────────────
# Conversion helpers
# ──────────────────────────────────────────────────────────────────────────────

def to_row_request_dc(m: ScoreRowRequestModel) -> "ScoreRowRequestDC":
    return ScoreRowRequestDC(
        adapter=m.adapter,
        row=m.row,
        weights_override=m.weights_override,
        context=m.context,
        caps_override=m.caps_override,
    )

def to_batch_request_dc(m: ScoreBatchRequestModel) -> "ScoreBatchRequestDC":
    return ScoreBatchRequestDC(
        adapter=m.adapter,
        rows=list(m.rows),  # normalize to list for scoring layer
        weights_override=m.weights_override,
        context=m.context,
        caps_override=m.caps_override,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Local dataclasses (framework-agnostic)
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ScoreRowRequestDC:
    adapter: str
    row: Row
    weights_override: Optional[Weights] = None
    context: Optional[Context] = None
    caps_override: Optional[Caps] = None


@dataclass(frozen=True)
class ScoreBatchRequestDC:
    adapter: str
    rows: Rows
    weights_override: Optional[Weights] = None
    context: Optional[Context] = None
    caps_override: Optional[Caps] = None
