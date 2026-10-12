"""Pydantic request and response models for the StatLine v4 gateway."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Row = Mapping[str, Any]
Rows = Sequence[Row]
Weights = dict[str, float]
WeightsArg = str | Weights
Penalties = dict[str, float]
Output = dict[str, Any]
Filters = dict[str, Any]
Caps = dict[str, float]
Context = dict[str, dict[str, float]]


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
    row: Row | None = Field(default=None, description="One input object.")
    rows: Rows | None = Field(default=None, description="A batch of input objects.")
    dataset: str | None = Field(
        default=None,
        description="Packaged CSV path from GET /v4/datasets.",
    )
    input_kind: Literal["raw", "mapped"] = Field(
        default="raw",
        description="Raw rows are mapped before scoring; mapped rows skip adapter mapping.",
    )
    weights: WeightsArg | None = Field(
        default=None,
        description="Weight profile name or bucket-to-weight override.",
    )
    penalties_override: Penalties | None = None
    output: Output | None = None
    profiles: Sequence[str] | None = Field(
        default=None,
        description="Score profiles to calculate. Omit or use 'all' for every profile.",
    )
    filters: Filters | None = None
    context: Context | None = None
    caps_override: Caps | None = None
    caps_mode: Literal["batch", "row"] = Field(
        default="batch",
        description="Use shared batch context or score each row independently.",
    )
    response_format: Literal["legacy", "compact"] = Field(
        default="legacy",
        description="Opt in to compact shared metadata. Legacy keeps row-level diagnostics; scored values are identical.",
    )
    include_mapped: bool = Field(
        default=False,
        description="Include the mapped rows used by the scorer in the response.",
    )
    dataset_limit: int | None = Field(
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
    def validate_source(self) -> ScoreIn:
        sources = [self.row is not None, self.rows is not None, self.dataset is not None]
        if sum(sources) != 1:
            raise ValueError("provide exactly one of row, rows, or dataset")
        if self.dataset is not None and self.input_kind != "raw":
            raise ValueError("dataset input_kind must be 'raw'")
        return self


class PairwiseIn(StrictModel):
    """Evaluate one registered native pairwise adapter using caller-provided rows."""

    adapter: str
    a: list[dict[str, Any]] = Field(max_length=1000, description="New match rows")
    b: list[dict[str, Any]] = Field(max_length=100000, description="Previous ratings")


class PairwiseOut(StrictModel):
    adapter: str
    adapter_version: str
    new_count: int
    previous_count: int
    results: list[dict[str, Any]]


class ScoreSource(StrictModel):
    kind: Literal["row", "rows", "dataset"]
    dataset: str | None = None


class ScoredResult(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(extra="allow")
    source_index: int = Field(
        ge=0,
        description="Original input position before filtering; stable when clients sort results.",
    )
    source_id: str | int | float | bool | None = Field(
        default=None,
        description="Adapter identity value when present. Duplicate names are allowed; use source_index for reconciliation.",
    )
    primary_profile: str
    pri: int
    scores: dict[str, int]
    components: dict[str, float] | None = None
    buckets: dict[str, float] | None = None
    weights: dict[str, float] | None = None
    context_used: dict[str, dict[str, float]] | None = None
    explanation_context: dict[str, Any] | None = None
    explanation: dict[str, Any] | None = Field(
        default=None,
        description="Opt-in raw metric attribution with per-profile aggregates and score transforms.",
    )


class ScoreOut(StrictModel):
    adapter: str
    adapter_version: str
    source: ScoreSource
    input_count: int
    mapped_count: int
    scored_count: int
    response_format: Literal["legacy", "compact"] = "legacy"
    shared: dict[str, Any] | None = Field(
        default=None,
        description="Exactly equal context_used, weights and explanation_context, only in compact mode. Row values override shared fields.",
    )
    results: list[ScoredResult] = Field(
        description="One row per surviving input, with original source_index and source_id when available."
    )
    mapped: list[dict[str, Any]] | None = None


class AdapterSummary(StrictModel):
    key: str
    title: str
    version: str
    aliases: list[str]
    dataset: str | None = None


class AdapterCatalog(StrictModel):
    adapters: list[AdapterSummary]
    cache: dict[str, int]


class AdapterOut(StrictModel):
    key: str
    title: str
    version: str
    author: str
    aliases: list[str]
    dataset: str | None = None
    source_id_field: str | None = None
    inputs: list[str]
    metrics: list[str]
    buckets: dict[str, dict[str, Any]]
    filters: dict[str, dict[str, Any]]
    dimensions: dict[str, dict[str, Any]]
    weights: dict[str, dict[str, float]]
    penalties: dict[str, dict[str, float]]
    score_profiles: dict[str, dict[str, Any]]


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
    rows: list[dict[str, Any]]


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
    resources: dict[str, str]


class ApiKeyRequestIn(StrictModel):
    owner: str | None = None
    scopes: Sequence[str] | None = None
    ttl_days: int | None = 30


class ApiKeyRequestDecisionIn(StrictModel):
    decided_by: str = "dev"
    note: str | None = None
    scopes: Sequence[str] | None = None


class EnrollIn(StrictModel):
    reg_token: str
    user: str
    email: str | None = None
    device_pub_b64: str
    meta: dict[str, Any] | None = None


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
