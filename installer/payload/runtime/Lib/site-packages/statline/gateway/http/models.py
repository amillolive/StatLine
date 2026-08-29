"""Pydantic request and response models for the StatLine v4 gateway."""

from __future__ import annotations

from typing import Any, ClassVar, Dict, Literal, Mapping, Optional, Sequence, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

Row = Mapping[str, Any]
Rows = Sequence[Row]
Weights = Dict[str, float]
WeightsArg = Union[str, Weights]
Penalties = Dict[str, float]
Output = Dict[str, Any]
Filters = Dict[str, Any]
Caps = Dict[str, float]
Context = Dict[str, Dict[str, float]]


class StrictModel(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


class SniffIn(StrictModel):
    headers: Sequence[str] = Field(
        description="CSV or object field names used to identify compatible adapters."
    )


class SniffOut(StrictModel):
    adapters: list[str]


class ScoreIn(StrictModel):
    """One v4 request shape for a row, a batch, or a packaged dataset."""

    adapter: str = Field(description="Adapter ID, file stem, or declared alias.")
    row: Optional[Row] = Field(default=None, description="One input object.")
    rows: Optional[Rows] = Field(default=None, description="A batch of input objects.")
    dataset: Optional[str] = Field(
        default=None,
        description="Packaged CSV path from GET /v4/datasets.",
    )
    input_kind: Literal["raw", "mapped"] = Field(
        default="raw",
        description="Raw rows are mapped before scoring; mapped rows skip adapter mapping.",
    )
    weights: Optional[WeightsArg] = Field(
        default=None,
        description="Weight profile name or bucket-to-weight override.",
    )
    penalties_override: Optional[Penalties] = None
    output: Optional[Output] = None
    filters: Optional[Filters] = None
    context: Optional[Context] = None
    caps_override: Optional[Caps] = None
    caps_mode: Literal["batch", "row"] = Field(
        default="batch",
        description="Use shared batch context or score each row independently.",
    )
    include_mapped: bool = Field(
        default=False,
        description="Include the mapped rows used by the scorer in the response.",
    )
    dataset_limit: Optional[int] = Field(
        default=None,
        ge=1,
        le=50_000,
        description="Optional safety limit when scoring a packaged dataset.",
    )

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "adapter": "eba.players",
                    "rows": [
                        {
                            "PLAYER": "Example Player",
                            "TEAM": "Example Team",
                            "GP": 16,
                            "PPG": 22.5,
                            "RPG": 5.2,
                            "APG": 7.8,
                        }
                    ],
                    "weights": "pri",
                },
                {
                    "adapter": "eba.players",
                    "dataset": "EBA_Elevate302/eba_hybrid_s1_players.csv",
                    "dataset_limit": 100,
                },
            ]
        },
    )

    @model_validator(mode="after")
    def validate_source(self) -> "ScoreIn":
        sources = [self.row is not None, self.rows is not None, self.dataset is not None]
        if sum(sources) != 1:
            raise ValueError("provide exactly one of row, rows, or dataset")
        if self.dataset is not None and self.input_kind != "raw":
            raise ValueError("dataset input_kind must be 'raw'")
        return self


class ScoreSource(StrictModel):
    kind: Literal["row", "rows", "dataset"]
    dataset: Optional[str] = None


class ScoreOut(StrictModel):
    adapter: str
    adapter_version: str
    source: ScoreSource
    input_count: int
    mapped_count: int
    scored_count: int
    results: list[Dict[str, Any]]
    mapped: Optional[list[Dict[str, Any]]] = None


class AdapterSummary(StrictModel):
    key: str
    title: str
    version: str
    aliases: list[str]
    dataset: Optional[str] = None


class AdapterCatalog(StrictModel):
    adapters: list[AdapterSummary]
    cache: Dict[str, int]


class AdapterOut(StrictModel):
    key: str
    title: str
    version: str
    author: str
    aliases: list[str]
    dataset: Optional[str] = None
    inputs: list[str]
    metrics: list[str]
    buckets: Dict[str, Dict[str, Any]]
    filters: Dict[str, Dict[str, Any]]
    dimensions: Dict[str, Dict[str, Any]]
    weights: Dict[str, Dict[str, float]]
    penalties: Dict[str, Dict[str, float]]
    score_profiles: Dict[str, Dict[str, Any]]


class DatasetSummary(StrictModel):
    path: str
    adapters: list[str]


class DatasetCatalog(StrictModel):
    datasets: list[DatasetSummary]


class DatasetPage(StrictModel):
    dataset: str
    columns: list[str]
    offset: int
    limit: int
    count: int
    has_more: bool
    rows: list[Dict[str, Any]]


class HealthOut(StrictModel):
    ok: bool
    version: str
    adapters: int


class ApiIndexOut(StrictModel):
    name: str
    version: str
    docs: str
    openapi: str
    health: str
    resources: Dict[str, str]


class ApiKeyRequestIn(StrictModel):
    owner: Optional[str] = None
    scopes: Optional[Sequence[str]] = None
    ttl_days: Optional[int] = 30


class ApiKeyRequestDecisionIn(StrictModel):
    decided_by: str = "dev"
    note: Optional[str] = None
    scopes: Optional[Sequence[str]] = None


class EnrollIn(StrictModel):
    reg_token: str
    user: str
    email: Optional[str] = None
    device_pub_b64: str
    meta: Optional[Dict[str, Any]] = None


__all__ = [
    "AdapterCatalog",
    "AdapterOut",
    "AdapterSummary",
    "ApiIndexOut",
    "ApiKeyRequestDecisionIn",
    "ApiKeyRequestIn",
    "DatasetCatalog",
    "DatasetPage",
    "DatasetSummary",
    "EnrollIn",
    "HealthOut",
    "ScoreIn",
    "ScoreOut",
    "ScoreSource",
    "SniffIn",
    "SniffOut",
]
