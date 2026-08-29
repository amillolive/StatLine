"""Canonical adapter definitions.

This module intentionally contains definitions only. Adapter behavior lives in
``statline.core.adapters`` function modules.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import (
    Any,
    Callable,
    Dict,
    Literal,
    Optional,
    Protocol,
    TypeAlias,
    Union,
    cast,
    runtime_checkable,
)

JSONScalar: TypeAlias = Union[str, int, float, bool, None]
JSONValue: TypeAlias = Union[JSONScalar, list["JSONValue"], dict[str, "JSONValue"]]
MetaScalar: TypeAlias = JSONScalar
MetaValue: TypeAlias = Union[MetaScalar, list[MetaScalar], dict[str, MetaScalar]]
Number: TypeAlias = Union[int, float]
Clamp: TypeAlias = tuple[float, float]
SniffKey: TypeAlias = Literal["require_any_headers", "require_all_headers"]
FilterType: TypeAlias = Literal["metric", "dimension"]
FilterOp: TypeAlias = Literal["<", ">", "<=", ">=", "==", "=", "!="]
FilterMode: TypeAlias = Literal["include-only", "exclude-only"]
SourceKind: TypeAlias = Literal["field", "expr", "const"]
TransformKind: TypeAlias = Literal["expr", "affine", "scale", "clip", "round", "custom"]
ScoreKind: TypeAlias = Literal["affine", "window"]
RowContext: TypeAlias = Mapping[str, object]
ExpressionEvaluator: TypeAlias = Callable[[RowContext, float], float]
MetricEvaluator: TypeAlias = Callable[[RowContext], float]


@dataclass(frozen=True, slots=True)
class AdapterMetadata:
    title: str
    id: str
    version: str
    author: str
    aliases: tuple[str, ...] = ()
    dataset: Optional[str] = None
    meta: Mapping[str, MetaValue] = dc_field(
        default_factory=cast(
            Callable[[], dict[str, MetaValue]],
            dict,
        )
    )


@dataclass(frozen=True, slots=True)
class DimensionSpec:
    values: tuple[str, ...] = ()
    description: str = ""
    strict: bool = True
    meta: dict[str, MetaValue] = dc_field(
        default_factory=cast(Callable[[], dict[str, MetaValue]], dict)
    )


@dataclass(frozen=True, slots=True)
class SniffSpec:
    require_any_headers: tuple[str, ...] = ()
    require_all_headers: tuple[str, ...] = ()
    meta: dict[str, MetaValue] = dc_field(
        default_factory=cast(Callable[[], dict[str, MetaValue]], dict)
    )


@dataclass(frozen=True, slots=True)
class FilterSpec:
    type: FilterType
    field: str
    accepts: tuple[FilterOp, ...] = ()
    modes: tuple[FilterMode, ...] = ("include-only", "exclude-only")
    description: str = ""
    meta: dict[str, MetaValue] = dc_field(
        default_factory=cast(Callable[[], dict[str, MetaValue]], dict)
    )


@dataclass(frozen=True, slots=True)
class BucketSpec:
    title: str = ""
    description: str = ""
    tags: tuple[str, ...] = ()
    hidden: bool = False
    meta: dict[str, MetaValue] = dc_field(
        default_factory=cast(Callable[[], dict[str, MetaValue]], dict)
    )


@dataclass(frozen=True, slots=True)
class SourceSpec:
    kind: SourceKind
    field: Optional[str] = None
    expr: Optional[str] = None
    const: Optional[float] = None


@dataclass(frozen=True, slots=True)
class TransformSpec:
    kind: TransformKind
    params: dict[str, MetaValue] = dc_field(
        default_factory=cast(Callable[[], dict[str, MetaValue]], dict)
    )


@dataclass(frozen=True, slots=True)
class MetricSpec:
    key: str
    source: Optional[SourceSpec] = None
    transform: Optional[TransformSpec] = None
    clamp: Optional[Clamp] = None
    bucket: Optional[str] = None
    invert: bool = False


@dataclass(frozen=True, slots=True)
class EffSpec:
    key: str
    make: str
    attempt: str
    bucket: str
    min_den: float = 1.0
    clamp: Optional[Clamp] = None
    invert: bool = False
    transform: Optional[TransformSpec] = None


@dataclass(frozen=True, slots=True)
class ScoreProfileSpec:
    kind: ScoreKind
    weights_profile: str
    lo: Optional[float] = None
    hi: Optional[float] = None
    out_lo: Optional[float] = None
    out_hi: Optional[float] = None
    pct_lo: Optional[float] = None
    pct_hi: Optional[float] = None


@dataclass(frozen=True, slots=True)
class AdapterSpec:
    metadata: AdapterMetadata
    dimensions: dict[str, DimensionSpec] = dc_field(
        default_factory=cast(Callable[[], dict[str, DimensionSpec]], dict)
    )
    sniff: SniffSpec = dc_field(default_factory=SniffSpec)
    filters: dict[str, FilterSpec] = dc_field(
        default_factory=cast(Callable[[], dict[str, FilterSpec]], dict)
    )
    buckets: dict[str, BucketSpec] = dc_field(
        default_factory=cast(Callable[[], dict[str, BucketSpec]], dict)
    )
    metrics: list[MetricSpec] = dc_field(default_factory=cast(Callable[[], list[MetricSpec]], list))
    weights: dict[str, dict[str, float]] = dc_field(
        default_factory=cast(Callable[[], dict[str, dict[str, float]]], dict)
    )
    penalties: dict[str, dict[str, float]] = dc_field(
        default_factory=cast(Callable[[], dict[str, dict[str, float]]], dict)
    )
    efficiency: list[EffSpec] = dc_field(default_factory=cast(Callable[[], list[EffSpec]], list))
    score_profiles: dict[str, ScoreProfileSpec] = dc_field(
        default_factory=cast(Callable[[], dict[str, ScoreProfileSpec]], dict)
    )


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    path: str
    message: str
    hint: Optional[str] = None


class AdapterValidationError(ValueError):
    def __init__(self, adapter_name: str, issues: list[ValidationIssue]):
        self.adapter_name = adapter_name
        self.issues = issues
        super().__init__(self._format())

    def _format(self) -> str:
        lines = [f"Adapter '{self.adapter_name}' failed validation:"]
        for issue in self.issues:
            hint = f" (hint: {issue.hint})" if issue.hint else ""
            lines.append(f" - {issue.path}: {issue.message}{hint}")
        return "\n".join(lines)


@runtime_checkable
class AdapterHooks(Protocol):
    def pre_map(self, row: Dict[str, Any]) -> Dict[str, Any]: ...
    def post_map(self, metrics: Dict[str, float]) -> Dict[str, float]: ...
    def sniff(self, headers: Iterable[str]) -> bool: ...


class NoOpHooks:
    def pre_map(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return row

    def post_map(self, metrics: Dict[str, float]) -> Dict[str, float]:
        return metrics

    def sniff(self, headers: Iterable[str]) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class CompiledMetric:
    key: str
    evaluate: MetricEvaluator


@dataclass(frozen=True, slots=True)
class CompiledEfficiency:
    key: str
    evaluate: MetricEvaluator


@dataclass(frozen=True, slots=True)
class CompiledAdapter:
    metadata: AdapterMetadata
    dimensions: Mapping[str, DimensionSpec]
    sniff: SniffSpec
    filters: Mapping[str, FilterSpec]
    score_profiles: Mapping[str, ScoreProfileSpec]
    metrics: tuple[MetricSpec, ...]
    buckets: Mapping[str, BucketSpec]
    weights: Mapping[str, Mapping[str, float]]
    penalties: Mapping[str, Mapping[str, float]]
    efficiency: tuple[EffSpec, ...]
    metric_plan: tuple[CompiledMetric, ...]
    efficiency_plan: tuple[CompiledEfficiency, ...]

    @property
    def key(self) -> str:
        return self.metadata.id

    @property
    def version(self) -> str:
        return self.metadata.version

    @property
    def aliases(self) -> tuple[str, ...]:
        return self.metadata.aliases

    @property
    def title(self) -> str:
        return self.metadata.title

    @property
    def author(self) -> str:
        return self.metadata.author

    @property
    def dataset(self) -> Optional[str]:
        return self.metadata.dataset

    @property
    def meta(self) -> Mapping[str, MetaValue]:
        return self.metadata.meta

    def map_raw(
        self,
        raw: Mapping[str, object],
        *,
        dataset_context: Optional[Mapping[str, object]] = None,
    ) -> dict[str, float]:
        from statline.core.adapters.compile import map_raw

        return map_raw(self, raw, dataset_context=dataset_context)


__all__ = [
    "AdapterHooks",
    "AdapterSpec",
    "AdapterValidationError",
    "BucketSpec",
    "Clamp",
    "CompiledAdapter",
    "CompiledEfficiency",
    "CompiledMetric",
    "DimensionSpec",
    "EffSpec",
    "ExpressionEvaluator",
    "FilterMode",
    "FilterOp",
    "FilterSpec",
    "FilterType",
    "JSONScalar",
    "JSONValue",
    "MetaScalar",
    "MetaValue",
    "MetricEvaluator",
    "MetricSpec",
    "NoOpHooks",
    "Number",
    "RowContext",
    "ScoreKind",
    "ScoreProfileSpec",
    "SniffKey",
    "SniffSpec",
    "SourceKind",
    "SourceSpec",
    "TransformKind",
    "TransformSpec",
    "ValidationIssue",
]
