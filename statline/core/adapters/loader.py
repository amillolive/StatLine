# statline/core/adapters/loader.py
from __future__ import annotations

import math
import os
import warnings
from collections.abc import Mapping as ABCMapping
from pathlib import Path
from typing import Mapping, Optional, Sequence, SupportsFloat, SupportsIndex, TypeAlias, cast

import yaml

from .types import (
    AdapterSpec,
    BucketSpec,
    Clamp,
    DimensionSpec,
    EffSpec,
    FilterMode,
    FilterOp,
    FilterSpec,
    FilterType,
    MetaScalar,
    MetaValue,
    MetricSpec,
    ScoreKind,
    ScoreProfileSpec,
    SniffSpec,
    SourceKind,
    SourceSpec,
    TransformKind,
    TransformSpec,
    validate_adapter,
)

_BASE = Path(__file__).parent / "defs"

# Fail-fast by default:
#   STATLINE_LOADER_STRICT="1" (default) -> raise on unknown keys / unknown buckets / invalid shapes
#   STATLINE_LOADER_STRICT="0" -> warn-and-continue where possible
_STRICT = os.environ.get("STATLINE_LOADER_STRICT", "1") not in ("0", "", "false", "False")


def _warn(msg: str) -> None:
    warnings.warn(f"[statline.loader] {msg}", RuntimeWarning, stacklevel=2)


_ConvertibleToFloat: TypeAlias = SupportsFloat | SupportsIndex | str | bytes | bytearray


def _finite_float(x: object, default: float = 0.0) -> float:
    """Coerce to finite float; warn and return default on failure/NaN/inf."""
    try:
        v = float(cast(_ConvertibleToFloat, x))
    except Exception:
        _warn(f"Non-numeric value '{x}' coerced to {default}")
        return default
    if not math.isfinite(v):
        _warn(f"Non-finite value '{x}' coerced to {default}")
        return default
    return v


# Allowed top-level keys in an adapter YAML (helps catch typos).
_ALLOWED_TOP_KEYS: set[str] = {
    "key",
    "version",
    "aliases",
    "title",
    "dimensions",
    "sniff",
    "filters",
    "buckets",
    "metrics",
    "weights",
    "penalties",
    "efficiency",
    "score_profiles",
}

_ALLOWED_BUCKET_KEYS: set[str] = {"title", "description", "tags", "hidden", "meta"}
_ALLOWED_DIM_KEYS: set[str] = {"values", "description", "strict", "meta"}
_ALLOWED_SNIFF_KEYS: set[str] = {"require_any_headers", "require_all_headers", "meta"}
_ALLOWED_FILTER_KEYS: set[str] = {
    "type",
    "field",
    "accepts",
    "modes",
    "values",  # back-compat alias for modes
    "description",
    "meta",
}
_ALLOWED_SOURCE_KEYS: set[str] = {"kind", "field", "expr", "const"}
_ALLOWED_TRANSFORM_KEYS: set[str] = {"kind", "expr", "params", "name"}  # allow legacy "name"
_ALLOWED_SCORE_PROFILE_KEYS: set[str] = {
    "kind",
    "weights_profile",
    "lo",
    "hi",
    "out_lo",
    "out_hi",
    "pct_lo",
    "pct_hi",
}
_ALLOWED_METRIC_KEYS: set[str] = {
    "key",
    "bucket",
    "clamp",
    "invert",
    "source",
    "transform",
}
_ALLOWED_EFF_KEYS: set[str] = {
    "key",
    "make",
    "attempt",
    "bucket",
    "min_den",
    "clamp",
    "invert",
    "transform",
}

# Literal “parsers” (avoid casts by returning the Literal types directly)
_FILTER_TYPE: dict[str, FilterType] = {"metric": "metric", "dimension": "dimension"}
_FILTER_OP: dict[str, FilterOp] = {
    "<": "<",
    ">": ">",
    "<=": "<=",
    ">=": ">=",
    "==": "==",
    "=": "==",   # alias → canonical
    "!=": "!=",
}
_FILTER_MODE: dict[str, FilterMode] = {"include-only": "include-only", "exclude-only": "exclude-only"}
_SOURCE_KIND: dict[str, SourceKind] = {"field": "field", "expr": "expr", "const": "const"}
_TRANSFORM_KIND: dict[str, TransformKind] = {
    "expr": "expr",
    "affine": "affine",
    "scale": "scale",
    "clip": "clip",
    "round": "round",
    "custom": "custom",
}
_SCORE_KIND: dict[str, ScoreKind] = {"affine": "affine", "window": "window"}


# ──────────────────────────────────────────────────────────────────────────────
# YAML boundary normalizers (reduce “Unknown” at the boundary)
# ──────────────────────────────────────────────────────────────────────────────

def _as_str_dict(obj: object, *, ctx: str) -> dict[str, object]:
    """Normalize a YAML mapping into dict[str, object]."""
    if obj is None:
        return {}
    if not isinstance(obj, ABCMapping):
        raise TypeError(f"{ctx} must be a mapping (dict), got {type(obj).__name__}")

    m = cast(Mapping[object, object], obj)
    out: dict[str, object] = {}
    for k, v in m.items():
        out[str(k)] = v
    return out


def _as_obj_list(obj: object, *, ctx: str) -> list[object]:
    """Normalize a YAML list into list[object]."""
    if obj is None:
        return []
    if not isinstance(obj, list):
        raise TypeError(f"{ctx} must be a list, got {type(obj).__name__}")
    return list(cast(list[object], obj))


def _as_str_tuple(obj: object) -> tuple[str, ...]:
    """Normalize string or list/tuple of values into tuple[str, ...]."""
    if obj is None:
        return ()
    if isinstance(obj, str):
        return (obj,)
    if isinstance(obj, (list, tuple)):
        seq = cast(Sequence[object], obj)
        out: list[str] = []
        for x in seq:
            sx = str(x)
            if sx:
                out.append(sx)
        return tuple(out)
    return ()


def _is_meta_scalar(x: object) -> bool:
    return x is None or isinstance(x, (str, int, float, bool))


def _coerce_meta_value(v: object, *, ctx: str) -> Optional[MetaValue]:
    """
    Coerce into shallow MetaValue:
      - scalar
      - list[scalar]
      - dict[str, scalar]
    """
    if _is_meta_scalar(v):
        return v  # type: ignore[return-value]

    if isinstance(v, list):
        seq = cast(list[object], v)
        items: list[MetaScalar] = []
        for i in seq:
            if not _is_meta_scalar(i):
                _warn(f"{ctx}: meta list contains non-scalar(s) — dropping")
                return None
            items.append(cast(MetaScalar, i))
        return items  # type: ignore[return-value]

    if isinstance(v, Mapping):
        m = cast(Mapping[object, object], v)
        out: dict[str, MetaScalar] = {}
        for k, vv in m.items():
            if not _is_meta_scalar(vv):
                _warn(f"{ctx}: meta dict contains non-scalar at '{k}' — dropping")
                return None
            out[str(k)] = cast(MetaScalar, vv)
        return out  # type: ignore[return-value]

    _warn(f"{ctx}: meta value type {type(v).__name__} unsupported — dropping")
    return None


def _coerce_meta_map(obj: object, *, ctx: str) -> dict[str, MetaValue]:
    """Coerce a mapping into dict[str, MetaValue] (shallow, tool-friendly)."""
    if obj is None:
        return {}
    if not isinstance(obj, Mapping):
        _warn(f"{ctx}: meta must be a mapping — dropping")
        return {}

    m = cast(Mapping[object, object], obj)
    out: dict[str, MetaValue] = {}
    for k, v in m.items():
        ks = str(k)
        mv = _coerce_meta_value(v, ctx=f"{ctx}.{ks}")
        if mv is not None:
            out[ks] = mv
    return out


def _read_yaml_for(name: str) -> dict[str, object]:
    p = _BASE / f"{name}.yaml"
    if not p.exists():
        p = _BASE / f"{name}.yml"
    if not p.exists():
        raise FileNotFoundError(
            f"Adapter spec not found: {name} (expected {name}.yaml or {name}.yml)"
        )

    try:
        loaded: object = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in '{p.name}': {e}") from e

    data = _as_str_dict(loaded, ctx=f"Top-level YAML for '{p.name}'")

    unknown = set(data.keys()).difference(_ALLOWED_TOP_KEYS)
    if unknown:
        msg = (
            f"Unknown top-level key(s) in adapter '{name}' ({p}): "
            f"{', '.join(sorted(unknown))}"
        )
        if _STRICT:
            raise KeyError(msg)
        _warn(msg + " — ignoring.")
        for k in unknown:
            data.pop(k, None)

    return data


def _require_keys(data: Mapping[str, object], name: str, *req: str) -> None:
    missing = [k for k in req if k not in data]
    if missing:
        raise KeyError(f"Adapter '{name}' is missing required key(s): {', '.join(missing)}")


def _as_clamp(v: object) -> Optional[Clamp]:
    """Normalize clamp configs to (lo, hi) or None. Swaps if lo > hi. Warns on bad forms."""
    if v is None or v is False:
        return None

    def _pair(lo: object, hi: object) -> Optional[Clamp]:
        try:
            a = float(cast(_ConvertibleToFloat, lo))
            b = float(cast(_ConvertibleToFloat, hi))
        except Exception:
            _warn(f"Clamp values '{lo}','{hi}' non-numeric — ignoring clamp")
            return None
        if not (math.isfinite(a) and math.isfinite(b)):
            _warn(f"Clamp values '{lo}','{hi}' non-finite — ignoring clamp")
            return None
        if a > b:
            a, b = b, a
        if a == b:
            _warn(f"Clamp with lo==hi ({a}) — ignoring clamp")
            return None
        return (a, b)

    if isinstance(v, Mapping):
        dv = _as_str_dict(cast(Mapping[object, object], v), ctx="clamp")
        if "lo" in dv and "hi" in dv:
            return _pair(dv["lo"], dv["hi"])

    if isinstance(v, (list, tuple)):
        seq = cast(Sequence[object], v)
        if len(seq) >= 2:
            return _pair(seq[0], seq[1])
        _warn(f"Clamp sequence too short: {v} — ignoring clamp")
        return None

    if isinstance(v, str):
        parts = v.replace(",", " ").replace("..", " ").split()
        if len(parts) >= 2:
            return _pair(parts[0], parts[1])
        _warn(f"Clamp string malformed: '{v}' — ignoring clamp")
        return None

    _warn(f"Unsupported clamp type {type(v).__name__} — ignoring clamp") # pyright: ignore[reportUnknownArgumentType]
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Coercers for typed dataclasses
# ──────────────────────────────────────────────────────────────────────────────

def _coerce_buckets(v: object, name: str) -> dict[str, BucketSpec]:
    vm = _as_str_dict(v, ctx=f"Adapter '{name}': 'buckets'")
    if not vm:
        raise ValueError(f"Adapter '{name}': 'buckets' cannot be empty")

    out: dict[str, BucketSpec] = {}
    for bk, bv in vm.items():
        if bv is None:
            out[bk] = BucketSpec()
            continue
        if not isinstance(bv, Mapping):
            msg = f"Adapter '{name}': bucket '{bk}' must be a mapping"
            if _STRICT:
                raise TypeError(msg)
            _warn(msg + " — using empty bucket.")
            out[bk] = BucketSpec()
            continue

        bvm = _as_str_dict(cast(Mapping[object, object], bv), ctx=f"Adapter '{name}': bucket '{bk}'")
        unknown = set(bvm.keys()).difference(_ALLOWED_BUCKET_KEYS)
        if unknown:
            msg = f"Adapter '{name}': bucket '{bk}' has unknown key(s): {', '.join(sorted(unknown))}"
            if _STRICT:
                raise KeyError(msg)
            _warn(msg + " — ignoring.")
            for k in unknown:
                bvm.pop(k, None)

        tags: tuple[str, ...] = ()
        tags_obj = bvm.get("tags")
        if isinstance(tags_obj, list):
            tags_seq = cast(list[object], tags_obj)
            tags = tuple(str(x) for x in tags_seq if str(x))

        out[bk] = BucketSpec(
            title=str(bvm.get("title", "")),
            description=str(bvm.get("description", "")),
            tags=tags,
            hidden=bool(bvm.get("hidden", False)),
            meta=_coerce_meta_map(bvm.get("meta"), ctx=f"Adapter '{name}': bucket '{bk}'.meta"),
        )

    return out


def _coerce_dimensions(v: object, name: str) -> dict[str, DimensionSpec]:
    """
    Loosened:
      - missing/None values => treated as []
      - if values empty and strict not explicitly provided => strict defaults to False (free-form)
    """
    if v is None:
        return {}
    vm = _as_str_dict(v, ctx=f"Adapter '{name}': 'dimensions'")
    out: dict[str, DimensionSpec] = {}

    for dk, dv in vm.items():
        if not isinstance(dv, Mapping):
            msg = f"Adapter '{name}': dimension '{dk}' must be a mapping"
            if _STRICT:
                raise TypeError(msg)
            _warn(msg + " — ignoring.")
            continue

        dvm = _as_str_dict(cast(Mapping[object, object], dv), ctx=f"Adapter '{name}': dimension '{dk}'")
        unknown = set(dvm.keys()).difference(_ALLOWED_DIM_KEYS)
        if unknown:
            msg = f"Adapter '{name}': dimension '{dk}' has unknown key(s): {', '.join(sorted(unknown))}"
            if _STRICT:
                raise KeyError(msg)
            _warn(msg + " — ignoring.")
            for k in unknown:
                dvm.pop(k, None)

        # values is optional now
        vals_obj = dvm.get("values", [])
        vals_out: list[str] = []

        if vals_obj is None:
            vals_obj = []
        if isinstance(vals_obj, (list, tuple)):
            vals = cast(Sequence[object], vals_obj)
            for x in vals:
                sx = str(x)
                if sx:
                    vals_out.append(sx)
        else:
            msg = f"Adapter '{name}': dimension '{dk}.values' must be a list if provided"
            if _STRICT:
                raise TypeError(msg)
            _warn(msg + " — treating as empty list.")
            vals_out = []

        strict_present = "strict" in dvm
        strict_val = bool(dvm.get("strict", True))
        if (not strict_present) and (len(vals_out) == 0):
            # free-form unless explicitly set
            strict_val = False

        out[dk] = DimensionSpec(
            values=tuple(vals_out),
            description=str(dvm.get("description", "")),
            strict=strict_val,
            meta=_coerce_meta_map(dvm.get("meta"), ctx=f"Adapter '{name}': dimension '{dk}'.meta"),
        )

    return out


def _coerce_sniff(v: object, name: str) -> SniffSpec:
    if v is None:
        return SniffSpec()

    vm = _as_str_dict(v, ctx=f"Adapter '{name}': 'sniff'")
    unknown = set(vm.keys()).difference(_ALLOWED_SNIFF_KEYS)
    if unknown:
        msg = f"Adapter '{name}': sniff has unknown key(s): {', '.join(sorted(unknown))}"
        if _STRICT:
            raise KeyError(msg)
        _warn(msg + " — ignoring.")
        for k in unknown:
            vm.pop(k, None)

    any_headers = vm.get("require_any_headers")
    all_headers = vm.get("require_all_headers")

    ra = _as_str_tuple(cast(object, any_headers)) if isinstance(any_headers, (list, tuple, str)) else ()
    rl = _as_str_tuple(cast(object, all_headers)) if isinstance(all_headers, (list, tuple, str)) else ()

    return SniffSpec(
        require_any_headers=ra,
        require_all_headers=rl,
        meta=_coerce_meta_map(vm.get("meta"), ctx=f"Adapter '{name}': sniff.meta"),
    )


def _coerce_filters(v: object, name: str) -> dict[str, FilterSpec]:
    if v is None:
        return {}
    vm = _as_str_dict(v, ctx=f"Adapter '{name}': 'filters'")
    out: dict[str, FilterSpec] = {}

    for fk, fv in vm.items():
        if not isinstance(fv, Mapping):
            msg = f"Adapter '{name}': filter '{fk}' must be a mapping"
            if _STRICT:
                raise TypeError(msg)
            _warn(msg + " — ignoring.")
            continue

        fvm = _as_str_dict(cast(Mapping[object, object], fv), ctx=f"Adapter '{name}': filter '{fk}'")
        unknown = set(fvm.keys()).difference(_ALLOWED_FILTER_KEYS)
        if unknown:
            msg = f"Adapter '{name}': filter '{fk}' has unknown key(s): {', '.join(sorted(unknown))}"
            if _STRICT:
                raise KeyError(msg)
            _warn(msg + " — ignoring.")
            for k in unknown:
                fvm.pop(k, None)

        ftype_s = str(fvm.get("type", "")).strip()
        ftype = _FILTER_TYPE.get(ftype_s)
        if ftype is None:
            msg = f"Adapter '{name}': filter '{fk}.type' must be 'metric' or 'dimension'"
            if _STRICT:
                raise TypeError(msg)
            _warn(msg + " — ignoring filter.")
            continue

        field = str(fvm.get("field", "")).strip()
        if not field:
            msg = f"Adapter '{name}': filter '{fk}.field' cannot be empty"
            if _STRICT:
                raise TypeError(msg)
            _warn(msg + " — ignoring filter.")
            continue

        accepts: list[FilterOp] = []
        accepts_obj = fvm.get("accepts")
        if isinstance(accepts_obj, (list, tuple)):
            for x in cast(Sequence[object], accepts_obj):
                op = _FILTER_OP.get(str(x))
                if op is not None:
                    accepts.append(op)

        modes: list[FilterMode] = []
        modes_obj = fvm.get("modes", fvm.get("values", ("include-only", "exclude-only")))
        if isinstance(modes_obj, (list, tuple)):
            for x in cast(Sequence[object], modes_obj):
                md = _FILTER_MODE.get(str(x))
                if md is not None:
                    modes.append(md)
        if not modes:
            modes = ["include-only", "exclude-only"]

        out[fk] = FilterSpec(
            type=ftype,
            field=field,
            accepts=tuple(accepts),
            modes=tuple(modes),
            description=str(fvm.get("description", "")),
            meta=_coerce_meta_map(fvm.get("meta"), ctx=f"Adapter '{name}': filter '{fk}'.meta"),
        )

    return out


def _coerce_source(v: object, *, ctx: str) -> SourceSpec:
    """
    Fail-fast in strict mode; returns a SourceSpec (never None).
    """
    if v is None:
        raise KeyError(f"{ctx}: source is required")

    if not isinstance(v, Mapping):
        msg = f"{ctx}: source must be a mapping (e.g. {{field: ppg}})"
        raise TypeError(msg)

    m = _as_str_dict(cast(Mapping[object, object], v), ctx=ctx)
    unknown = set(m.keys()).difference(_ALLOWED_SOURCE_KEYS)
    if unknown:
        msg = f"{ctx}: source has unknown key(s): {', '.join(sorted(unknown))}"
        if _STRICT:
            raise KeyError(msg)
        _warn(msg + " — ignoring unknown source keys.")
        for k in unknown:
            m.pop(k, None)

    kind_any = m.get("kind")
    if kind_any is not None:
        kind = _SOURCE_KIND.get(str(kind_any).strip())
        if kind is None:
            raise TypeError(f"{ctx}: source.kind must be field|expr|const")
        return SourceSpec(
            kind=kind,
            field=str(m.get("field")) if m.get("field") is not None else None,
            expr=str(m.get("expr")) if m.get("expr") is not None else None,
            const=_finite_float(m.get("const"), default=0.0) if m.get("const") is not None else None,
        )

    has_field = "field" in m
    has_expr = "expr" in m
    has_const = "const" in m
    if (1 if has_field else 0) + (1 if has_expr else 0) + (1 if has_const else 0) != 1:
        raise TypeError(f"{ctx}: source must have exactly one of: field|expr|const")

    if has_field:
        return SourceSpec(kind="field", field=str(m["field"]))
    if has_expr:
        return SourceSpec(kind="expr", expr=str(m["expr"]))
    return SourceSpec(kind="const", const=_finite_float(m["const"], default=0.0))


def _coerce_transform(v: object, *, ctx: str) -> Optional[TransformSpec]:
    if v is None:
        return None
    if not isinstance(v, Mapping):
        msg = f"{ctx}: transform must be a mapping"
        if _STRICT:
            raise TypeError(msg)
        _warn(msg + " — ignoring transform.")
        return None

    m = _as_str_dict(cast(Mapping[object, object], v), ctx=ctx)

    # Shorthand: { expr: "..." } (no kind)
    if "expr" in m and "kind" not in m:
        return TransformSpec(kind="expr", params={"expr": str(m["expr"])})

    # Legacy shorthand: { name: "linear", params: {...} } -> kind="custom"
    if "name" in m and "kind" not in m:
        params: dict[str, MetaValue] = {}
        params["name"] = str(m.get("name", ""))
        params_obj = m.get("params")
        if isinstance(params_obj, Mapping):
            pm = _as_str_dict(cast(Mapping[object, object], params_obj), ctx=f"{ctx}: transform.params")
            for kk, vv in pm.items():
                mv = _coerce_meta_value(vv, ctx=f"{ctx}: transform.params.{kk}")
                if mv is not None:
                    params[kk] = mv
        return TransformSpec(kind="custom", params=params)

    kind_any = m.get("kind")
    if kind_any is None:
        msg = f"{ctx}: transform missing 'kind' (or use shorthand {{expr: ...}})"
        if _STRICT:
            raise KeyError(msg)
        _warn(msg + " — ignoring transform.")
        return None

    kind = _TRANSFORM_KIND.get(str(kind_any).strip())
    if kind is None:
        msg = f"{ctx}: transform.kind invalid '{kind_any}'"
        if _STRICT:
            raise TypeError(msg)
        _warn(msg + " — ignoring transform.")
        return None

    unknown = set(m.keys()).difference(_ALLOWED_TRANSFORM_KEYS)
    if unknown:
        msg = f"{ctx}: transform has unknown key(s): {', '.join(sorted(unknown))}"
        if _STRICT:
            raise KeyError(msg)
        _warn(msg + " — ignoring unknown keys.")

    params_out: dict[str, MetaValue] = {}
    for k, val in m.items():
        if k == "kind":
            continue
        if k == "params" and isinstance(val, Mapping):
            pm = _as_str_dict(cast(Mapping[object, object], val), ctx=f"{ctx}: transform.params")
            for kk, vv in pm.items():
                mv = _coerce_meta_value(vv, ctx=f"{ctx}: transform.params.{kk}")
                if mv is not None:
                    params_out[kk] = mv
        else:
            mv = _coerce_meta_value(val, ctx=f"{ctx}: transform.{k}")
            if mv is not None:
                params_out[k] = mv

    return TransformSpec(kind=kind, params=params_out)


def _coerce_score_profiles(v: object, name: str) -> dict[str, ScoreProfileSpec]:
    if v is None:
        return {}
    vm = _as_str_dict(v, ctx=f"Adapter '{name}': 'score_profiles'")
    out: dict[str, ScoreProfileSpec] = {}

    def _opt_float(pvm: dict[str, object], key: str) -> Optional[float]:
        if key not in pvm or pvm[key] is None:
            return None
        return _finite_float(pvm[key], default=0.0)

    for pk, pv in vm.items():
        if not isinstance(pv, Mapping):
            msg = f"Adapter '{name}': score profile '{pk}' must be a mapping"
            if _STRICT:
                raise TypeError(msg)
            _warn(msg + " — ignoring.")
            continue

        pvm = _as_str_dict(cast(Mapping[object, object], pv), ctx=f"Adapter '{name}': score profile '{pk}'")
        unknown = set(pvm.keys()).difference(_ALLOWED_SCORE_PROFILE_KEYS)
        if unknown:
            msg = f"Adapter '{name}': score profile '{pk}' has unknown key(s): {', '.join(sorted(unknown))}"
            if _STRICT:
                raise KeyError(msg)
            _warn(msg + " — ignoring.")
            for k in unknown:
                pvm.pop(k, None)

        kind = _SCORE_KIND.get(str(pvm.get("kind", "")).strip())
        if kind is None:
            msg = f"Adapter '{name}': score profile '{pk}.kind' must be affine|window"
            if _STRICT:
                raise TypeError(msg)
            _warn(msg + " — ignoring score profile.")
            continue

        wp = str(pvm.get("weights_profile", "")).strip()
        if not wp:
            msg = f"Adapter '{name}': score profile '{pk}.weights_profile' missing/empty"
            if _STRICT:
                raise KeyError(msg)
            _warn(msg + " — ignoring score profile.")
            continue

        out[pk] = ScoreProfileSpec(
            kind=kind,
            weights_profile=wp,
            lo=_opt_float(pvm, "lo"),
            hi=_opt_float(pvm, "hi"),
            out_lo=_opt_float(pvm, "out_lo"),
            out_hi=_opt_float(pvm, "out_hi"),
            pct_lo=_opt_float(pvm, "pct_lo"),
            pct_hi=_opt_float(pvm, "pct_hi"),
        )

    return out


def _uniform_weights(bucket_names: Sequence[str]) -> dict[str, dict[str, float]]:
    keys = list(bucket_names)
    n = len(keys) or 1
    w = 1.0 / n
    return {"pri": {k: w for k in keys}}


def load_spec(name: str) -> AdapterSpec:
    data = _read_yaml_for(name)
    _require_keys(data, name, "key", "version", "buckets", "metrics")

    key = str(data["key"])
    version = str(data["version"])
    title = str(data.get("title", key))
    aliases = _as_str_tuple(data.get("aliases"))

    # Typed metadata
    dimensions = _coerce_dimensions(data.get("dimensions"), name)
    sniff = _coerce_sniff(data.get("sniff"), name)
    filters = _coerce_filters(data.get("filters"), name)
    score_profiles = _coerce_score_profiles(data.get("score_profiles"), name)

    # Typed buckets
    buckets = _coerce_buckets(data["buckets"], name)
    bucket_names = set(buckets.keys())

    # Weights (optional; default uniform pri)
    weights_raw = data.get("weights")
    if weights_raw is None:
        weights_out = _uniform_weights(sorted(bucket_names))
    else:
        weights_map = _as_str_dict(weights_raw, ctx=f"Adapter '{name}': 'weights'")
        weights_out: dict[str, dict[str, float]] = {}
        for profile, bw_obj in weights_map.items():
            bw = _as_str_dict(bw_obj, ctx=f"Adapter '{name}': weights profile '{profile}'")

            inner: dict[str, float] = {bk: 0.0 for bk in bucket_names}
            for b, v in bw.items():
                if b not in bucket_names:
                    msg = f"Adapter '{name}': weights profile '{profile}' references unknown bucket '{b}'"
                    if _STRICT:
                        raise KeyError(msg)
                    _warn(msg + " — treating as 0.0 and ignoring.")
                    continue
                inner[b] = _finite_float(v, default=0.0)
            weights_out[profile] = inner

    # Penalties (optional)
    penalties_map = _as_str_dict(data.get("penalties"), ctx=f"Adapter '{name}': 'penalties'")
    penalties: dict[str, dict[str, float]] = {}
    for profile, pw_obj in penalties_map.items():
        pw = _as_str_dict(pw_obj, ctx=f"Adapter '{name}': penalties profile '{profile}'")
        inner: dict[str, float] = {}
        for b, v in pw.items():
            if b not in bucket_names:
                msg = f"Adapter '{name}': penalties profile '{profile}' references unknown bucket '{b}'"
                if _STRICT:
                    raise KeyError(msg)
                _warn(msg + " — dropping penalty.")
                continue
            inner[b] = _finite_float(v, default=0.0)
        penalties[profile] = inner

    # Metrics (fail-fast source; check unknown keys; strict unknown bucket)
    metrics_items = _as_obj_list(data["metrics"], ctx=f"Adapter '{name}': 'metrics'")
    metrics: list[MetricSpec] = []
    seen_keys: set[str] = set()

    for i, m_item in enumerate(metrics_items):
        if not isinstance(m_item, Mapping):
            raise TypeError(f"Adapter '{name}': metrics[{i}] must be a mapping")
        m = _as_str_dict(cast(Mapping[object, object], m_item), ctx=f"Adapter '{name}': metrics[{i}]")

        unknown = set(m.keys()).difference(_ALLOWED_METRIC_KEYS)
        if unknown:
            msg = f"Adapter '{name}': metrics[{i}] has unknown key(s): {', '.join(sorted(unknown))}"
            if _STRICT:
                raise KeyError(msg)
            _warn(msg + " — ignoring unknown keys.")
            for k in unknown:
                m.pop(k, None)

        if "key" not in m:
            raise KeyError(f"Adapter '{name}': metrics[{i}] missing 'key'")

        mkey = str(m["key"]).strip()
        if not mkey:
            raise ValueError(f"Adapter '{name}': metrics[{i}].key cannot be empty")

        if mkey in seen_keys:
            msg = f"Adapter '{name}': duplicate metric key '{mkey}'"
            if _STRICT:
                raise KeyError(msg)
            _warn(msg + " — keeping first, skipping duplicate.")
            continue
        seen_keys.add(mkey)

        bucket_name: Optional[str] = None
        bucket_val = m.get("bucket")
        if bucket_val is not None:
            bname = str(bucket_val).strip()
            if bname:
                if bname not in bucket_names:
                    msg = f"Adapter '{name}': metric '{mkey}' references unknown bucket '{bname}'"
                    if _STRICT:
                        raise KeyError(msg)
                    _warn(msg + " — treating as unscored telemetry (no bucket).")
                else:
                    bucket_name = bname

        # source is REQUIRED (fail-fast)
        src = _coerce_source(m.get("source"), ctx=f"Adapter '{name}': metric '{mkey}'")

        metrics.append(
            MetricSpec(
                key=mkey,
                bucket=bucket_name,
                clamp=_as_clamp(m.get("clamp")),
                invert=bool(m.get("invert", False)),
                source=src,
                transform=_coerce_transform(m.get("transform"), ctx=f"Adapter '{name}': metric '{mkey}'"),
            )
        )

    # Efficiency (unknown keys check; strict unknown bucket)
    eff_any = data.get("efficiency")
    eff_items = _as_obj_list(eff_any, ctx=f"Adapter '{name}': 'efficiency'") if eff_any is not None else []
    eff_list: list[EffSpec] = []
    seen_eff: set[str] = set()

    for i, e_item in enumerate(eff_items):
        if not isinstance(e_item, Mapping):
            raise TypeError(f"Adapter '{name}': efficiency[{i}] must be a mapping")
        e = _as_str_dict(cast(Mapping[object, object], e_item), ctx=f"Adapter '{name}': efficiency[{i}]")

        unknown = set(e.keys()).difference(_ALLOWED_EFF_KEYS)
        if unknown:
            msg = f"Adapter '{name}': efficiency[{i}] has unknown key(s): {', '.join(sorted(unknown))}"
            if _STRICT:
                raise KeyError(msg)
            _warn(msg + " — ignoring unknown keys.")
            for k in unknown:
                e.pop(k, None)

        for req in ("key", "make", "attempt", "bucket"):
            if req not in e:
                raise KeyError(f"Adapter '{name}': efficiency[{i}] missing '{req}'")

        ekey = str(e["key"]).strip()
        if not ekey:
            raise ValueError(f"Adapter '{name}': efficiency[{i}].key cannot be empty")

        if ekey in seen_eff:
            msg = f"Adapter '{name}': duplicate efficiency key '{ekey}'"
            if _STRICT:
                raise KeyError(msg)
            _warn(msg + " — keeping first, skipping duplicate.")
            continue
        seen_eff.add(ekey)

        ebucket = str(e["bucket"]).strip()
        if ebucket not in bucket_names:
            msg = f"Adapter '{name}': efficiency '{ekey}' references unknown bucket '{ebucket}'"
            if _STRICT:
                raise KeyError(msg)
            _warn(msg + " — skipping efficiency item.")
            continue

        eff_list.append(
            EffSpec(
                key=ekey,
                make=str(e["make"]),
                attempt=str(e["attempt"]),
                bucket=ebucket,
                min_den=_finite_float(e.get("min_den", 1.0), default=1.0),
                clamp=_as_clamp(e.get("clamp")),
                invert=bool(e.get("invert", False)),
                transform=_coerce_transform(e.get("transform"), ctx=f"Adapter '{name}': efficiency '{ekey}'"),
            )
        )

    spec = AdapterSpec(
        key=key,
        version=version,
        aliases=aliases,
        title=title,
        dimensions=dimensions,
        sniff=sniff,
        filters=filters,
        buckets=buckets,
        metrics=metrics,
        weights=weights_out,
        penalties=penalties,
        efficiency=eff_list,
        score_profiles=score_profiles,
    )

    validate_adapter(spec)
    return spec


__all__ = ["load_spec"]
