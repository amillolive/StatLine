# statline/core/adapters/types.py
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Literal, Optional, TypeAlias, Union, cast

# ──────────────────────────────────────────────────────────────────────────────
# Typed "YAML value" (instead of Any)
# ──────────────────────────────────────────────────────────────────────────────

JSONScalar: TypeAlias = Union[str, int, float, bool, None]
JSONValue: TypeAlias = Union[
    JSONScalar, list["JSONValue"], dict[str, "JSONValue"]
]  # ok for IO/boundaries

# Tool-friendly metadata values (NON-RECURSIVE: avoids Pylance "partially unknown")
MetaScalar: TypeAlias = JSONScalar
MetaValue: TypeAlias = Union[
    MetaScalar,
    list[MetaScalar],
    dict[str, MetaScalar],
]

Number: TypeAlias = Union[int, float]
Clamp: TypeAlias = tuple[float, float]


def _meta_dict() -> dict[str, MetaValue]:
    # Gives Pylance a concrete factory return type (no dict[Unknown, Unknown])
    return {}


# ──────────────────────────────────────────────────────────────────────────────
# Metadata sections: fully typed
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DimensionSpec:
    # Example: map: { values: [MapA, MapB] }
    values: tuple[str, ...] = ()
    description: str = ""
    strict: bool = True
    meta: dict[str, MetaValue] = dc_field(default_factory=_meta_dict)


SniffKey: TypeAlias = Literal["require_any_headers", "require_all_headers"]


@dataclass(frozen=True, slots=True)
class SniffSpec:
    # Example: sniff: { require_any_headers: [...] }
    require_any_headers: tuple[str, ...] = ()
    require_all_headers: tuple[str, ...] = ()
    meta: dict[str, MetaValue] = dc_field(default_factory=_meta_dict)


FilterType: TypeAlias = Literal["metric", "dimension"]
FilterOp: TypeAlias = Literal["<", ">", "<=", ">=", "==", "=", "!="]
FilterMode: TypeAlias = Literal["include-only", "exclude-only"]


@dataclass(frozen=True, slots=True)
class FilterSpec:
    # Example:
    # gp: { type: metric, field: gp, accepts: ["<", ...], modes: ["include-only", "exclude-only"] }
    type: FilterType
    field: str
    accepts: tuple[FilterOp, ...] = ()
    modes: tuple[FilterMode, ...] = ("include-only", "exclude-only")
    description: str = ""
    meta: dict[str, MetaValue] = dc_field(default_factory=_meta_dict)


@dataclass(frozen=True, slots=True)
class BucketSpec:
    # YAML allows: bucket_name: {}  → becomes an "empty" BucketSpec
    title: str = ""
    description: str = ""
    tags: tuple[str, ...] = ()
    hidden: bool = False
    meta: dict[str, MetaValue] = dc_field(default_factory=_meta_dict)


# ──────────────────────────────────────────────────────────────────────────────
# Metric primitives: typed source & transform
# ──────────────────────────────────────────────────────────────────────────────

SourceKind: TypeAlias = Literal["field", "expr", "const"]


@dataclass(frozen=True, slots=True)
class SourceSpec:
    # Supports:
    #   { field: ppg }        → kind=field
    #   { expr: "a+b" }       → kind=expr
    #   { const: 1.0 }        → kind=const
    kind: SourceKind
    field: Optional[str] = None
    expr: Optional[str] = None
    const: Optional[float] = None


TransformKind: TypeAlias = Literal["expr", "affine", "scale", "clip", "round", "custom"]


@dataclass(frozen=True, slots=True)
class TransformSpec:
    # Keep it typed *and* extensible:
    #   { expr: "..." }                    → kind=expr, params={"expr": "..."}
    #   { kind: affine, a: 1.2, b: 0.3 }   → kind=affine, params typed as MetaValue
    #   { kind: custom, name: "zscore", ... }
    kind: TransformKind
    params: dict[str, MetaValue] = dc_field(default_factory=_meta_dict)


ScoreKind: TypeAlias = Literal["affine", "window"]


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
    weights_profile: str  # must exist in AdapterSpec.weights

    # affine params
    lo: Optional[float] = None
    hi: Optional[float] = None

    # window params
    out_lo: Optional[float] = None
    out_hi: Optional[float] = None
    pct_lo: Optional[float] = None
    pct_hi: Optional[float] = None


# ──────────────────────────────────────────────────────────────────────────────
# Adapter spec (fully typed, no Any)
# ──────────────────────────────────────────────────────────────────────────────


def _dict_str__dim() -> dict[str, DimensionSpec]:
    return {}


def _dict_str__filter() -> dict[str, FilterSpec]:
    return {}


def _dict_str__bucket() -> dict[str, BucketSpec]:
    return {}


def _list_metrics() -> list[MetricSpec]:
    return []


def _list_eff() -> list[EffSpec]:
    return []


def _dict_str__weights() -> dict[str, dict[str, float]]:
    return {}


def _dict_str__score_profiles() -> dict[str, ScoreProfileSpec]:
    return {}


@dataclass(frozen=True, slots=True)
class AdapterSpec:
    """
    Top-level adapter specification.

    ✅ Everything typed (dataclasses + Literals where it matters)
    ✅ Meta is shallow (MetaValue) to keep tool + editor inference clean
    """

    key: str
    version: str
    aliases: tuple[str, ...] = ()
    title: str = ""

    # metadata
    dimensions: dict[str, DimensionSpec] = dc_field(default_factory=_dict_str__dim)
    sniff: SniffSpec = dc_field(default_factory=SniffSpec)
    filters: dict[str, FilterSpec] = dc_field(default_factory=_dict_str__filter)

    # scoring spec
    buckets: dict[str, BucketSpec] = dc_field(default_factory=_dict_str__bucket)
    metrics: list[MetricSpec] = dc_field(default_factory=_list_metrics)
    weights: dict[str, dict[str, float]] = dc_field(default_factory=_dict_str__weights)
    penalties: dict[str, dict[str, float]] = dc_field(default_factory=_dict_str__weights)
    efficiency: list[EffSpec] = dc_field(default_factory=_list_eff)
    score_profiles: dict[str, ScoreProfileSpec] = dc_field(
        default_factory=_dict_str__score_profiles
    )


# ──────────────────────────────────────────────────────────────────────────────
# Validation (fast + opinionated)
# ──────────────────────────────────────────────────────────────────────────────


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
        for it in self.issues:
            lines.append(f" - {it.path}: {it.message}" + (f" (hint: {it.hint})" if it.hint else ""))
        return "\n".join(lines)


def validate_adapter(spec: AdapterSpec) -> None:
    issues: list[ValidationIssue] = []

    if not spec.key.strip():
        issues.append(ValidationIssue("key", "Missing or empty key."))
    if not spec.version.strip():
        issues.append(ValidationIssue("version", "Missing or empty version."))

    bucket_keys = set(spec.buckets.keys())

    # metrics
    seen_m: set[str] = set()
    for i, m in enumerate(spec.metrics):
        p = f"metrics[{i}]"
        if m.key in seen_m:
            issues.append(ValidationIssue(f"{p}.key", f"Duplicate metric key '{m.key}'."))
        seen_m.add(m.key)

        if m.bucket is not None and m.bucket not in bucket_keys:
            issues.append(
                ValidationIssue(
                    f"{p}.bucket",
                    f"Unknown bucket '{m.bucket}'.",
                    hint="Define it under buckets: or fix the metric.bucket.",
                )
            )

        if m.clamp is not None and not (m.clamp[0] < m.clamp[1]):
            issues.append(ValidationIssue(f"{p}.clamp", "Clamp must be (lo, hi) with lo < hi."))

    # efficiency
    seen_e: set[str] = set()
    for i, e in enumerate(spec.efficiency):
        p = f"efficiency[{i}]"
        if e.key in seen_e:
            issues.append(ValidationIssue(f"{p}.key", f"Duplicate efficiency key '{e.key}'."))
        seen_e.add(e.key)

        if e.bucket not in bucket_keys:
            issues.append(ValidationIssue(f"{p}.bucket", f"Unknown bucket '{e.bucket}'."))

        if e.clamp is not None and not (e.clamp[0] < e.clamp[1]):
            issues.append(ValidationIssue(f"{p}.clamp", "Clamp must be (lo, hi) with lo < hi."))

        if e.min_den < 0:
            issues.append(ValidationIssue(f"{p}.min_den", "min_den must be >= 0."))

    # score profiles
    for name, sp in spec.score_profiles.items():
        p = f"score_profiles.{name}"
        if sp.weights_profile not in spec.weights:
            issues.append(
                ValidationIssue(
                    f"{p}.weights_profile",
                    f"Unknown weights profile '{sp.weights_profile}'.",
                    hint="Add it under weights: or correct the reference.",
                )
            )

        if sp.kind == "affine":
            if sp.lo is None or sp.hi is None:
                issues.append(ValidationIssue(p, "Affine profile requires lo and hi."))
            elif not (sp.lo < sp.hi):
                issues.append(ValidationIssue(p, "Affine requires lo < hi."))

        elif sp.kind == "window":
            req = ("out_lo", "out_hi", "pct_lo", "pct_hi")
            missing = [k for k in req if getattr(sp, k) is None]
            if missing:
                issues.append(ValidationIssue(p, f"Window profile missing {missing}."))
            else:
                # At this point they're not None; cast keeps type-checkers happy.
                if not cast(float, sp.out_lo) < cast(float, sp.out_hi):
                    issues.append(ValidationIssue(p, "Window requires out_lo < out_hi."))
                if not cast(float, sp.pct_lo) < cast(float, sp.pct_hi):
                    issues.append(ValidationIssue(p, "Window requires pct_lo < pct_hi."))

    if issues:
        raise AdapterValidationError(spec.key or "<unknown>", issues)


__all__ = [
    "JSONValue",
    "DimensionSpec",
    "SniffSpec",
    "FilterSpec",
    "BucketSpec",
    "SourceSpec",
    "TransformSpec",
    "MetricSpec",
    "EffSpec",
    "ScoreProfileSpec",
    "AdapterSpec",
    "ValidationIssue",
    "AdapterValidationError",
    "validate_adapter",
]
