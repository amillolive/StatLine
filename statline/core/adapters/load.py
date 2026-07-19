"""Load and validate adapter YAML specifications."""

from __future__ import annotations

import math
import os
import warnings
from collections.abc import Mapping as ABCMapping
from pathlib import Path
from typing import Mapping, Optional, Sequence, SupportsFloat, SupportsIndex, TypeAlias, cast

import yaml

from statline.core.adapters.validate import validate_adapter
from statline.core.types.adapters import (
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
        value = float(cast(_ConvertibleToFloat, x))
    except Exception:
        _warn(f"Non-numeric value '{x}' coerced to {default}")
        return default
    if not math.isfinite(value):
        _warn(f"Non-finite value '{x}' coerced to {default}")
        return default
    return value


def _config_float(value: object, *, ctx: str) -> float:
    """Parse a required finite configuration number."""
    try:
        parsed = float(cast(_ConvertibleToFloat, value))
    except Exception as error:
        message = f"{ctx} must be numeric, got {value!r}"
        if _STRICT:
            raise TypeError(message) from error
        _warn(message + " — using 0.0.")
        return 0.0
    if not math.isfinite(parsed):
        message = f"{ctx} must be finite, got {value!r}"
        if _STRICT:
            raise ValueError(message)
        _warn(message + " — using 0.0.")
        return 0.0
    return parsed


def _config_bool(value: object, *, ctx: str, default: bool = False) -> bool:
    """Parse YAML and string booleans without treating 'false' as truthy."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    message = f"{ctx} must be a boolean, got {value!r}"
    if _STRICT:
        raise TypeError(message)
    _warn(message + f" — using {default}.")
    return default


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
_ALLOWED_TRANSFORM_KEYS: set[str] = {"kind", "expr", "params", "name"}  # structural keys
_TRANSFORM_PARAM_KEYS_BY_KIND: dict[str, set[str]] = {
    "expr": {"expr"},
    "affine": {"a", "b", "scale", "offset"},
    "scale": {"scale"},
    "clip": {"lo", "hi"},
    "round": {"ndigits"},
    "custom": {"name", "scale", "offset", "cap", "slope", "lo", "hi", "by"},
}
_CUSTOM_TRANSFORM_KEYS: dict[str, set[str]] = {
    "linear": {"name", "scale", "offset"},
    "capped_linear": {"name", "cap"},
    "minmax": {"name", "lo", "hi"},
    "pct01": {"name", "by"},
    "softcap": {"name", "cap", "slope"},
    "log1p": {"name", "scale"},
}
_REQUIRED_TRANSFORM_KEYS: dict[str, tuple[str, ...]] = {
    "expr": ("expr",),
    "scale": ("scale",),
    "clip": ("lo", "hi"),
    "round": ("ndigits",),
    "custom": ("name",),
}
_REQUIRED_CUSTOM_KEYS: dict[str, tuple[str, ...]] = {
    "capped_linear": ("cap",),
    "minmax": ("lo", "hi"),
    "softcap": ("cap",),
}
_NUMERIC_TRANSFORM_KEYS = {"a", "b", "scale", "offset", "cap", "slope", "lo", "hi", "by"}
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
    "=": "==",  # alias → canonical
    "!=": "!=",
}
_FILTER_MODE: dict[str, FilterMode] = {
    "include-only": "include-only",
    "exclude-only": "exclude-only",
}
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
    """Normalize a string sequence, stripping and dropping empty values."""
    if obj is None:
        return ()
    values = (obj,) if isinstance(obj, str) else obj
    if not isinstance(values, (list, tuple)):
        return ()
    output: list[str] = []
    for value in cast(Sequence[object], values):
        stripped = str(value).strip()
        if stripped:
            output.append(stripped)
    return tuple(output)


def _coerce_aliases(obj: object, *, key: str, ctx: str) -> tuple[str, ...]:
    """Return unique, normalized aliases while removing the primary key alias."""
    if obj is None:
        return ()
    values = (obj,) if isinstance(obj, str) else obj
    if not isinstance(values, (list, tuple)):
        message = f"{ctx} must be a string or list of strings"
        if _STRICT:
            raise TypeError(message)
        _warn(message + " — ignoring aliases.")
        return ()

    primary = key.casefold()
    seen: set[str] = set()
    aliases: list[str] = []
    for value in cast(Sequence[object], values):
        if not isinstance(value, str):
            message = f"{ctx} contains non-string alias {value!r}"
            if _STRICT:
                raise TypeError(message)
            _warn(message + " — dropping it.")
            continue
        alias = value.strip()
        if not alias:
            continue
        normalized = alias.casefold()
        if normalized == primary:
            continue
        if normalized in seen:
            message = f"{ctx} contains duplicate alias '{alias}'"
            if _STRICT:
                raise ValueError(message)
            _warn(message + " — keeping the first.")
            continue
        seen.add(normalized)
        aliases.append(alias)
    return tuple(aliases)


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
        return items

    if isinstance(v, Mapping):
        m = cast(Mapping[object, object], v)
        out: dict[str, MetaScalar] = {}
        for k, vv in m.items():
            if not _is_meta_scalar(vv):
                _warn(f"{ctx}: meta dict contains non-scalar at '{k}' — dropping")
                return None
            out[str(k)] = cast(MetaScalar, vv)
        return out

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


def _read_yaml_for(source: str | Path) -> dict[str, object]:
    if isinstance(source, Path):
        path = source
        name = path.stem
    else:
        name = str(source).strip()
        path = _BASE / f"{name}.yaml"
        if not path.exists():
            path = _BASE / f"{name}.yml"
    if not path.is_file():
        raise FileNotFoundError(
            f"Adapter spec not found: {name} (expected {name}.yaml or {name}.yml)"
        )

    try:
        loaded: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ValueError(f"Invalid YAML in '{path.name}': {error}") from error

    data = _as_str_dict(loaded, ctx=f"Top-level YAML for '{path.name}'")
    unknown = set(data).difference(_ALLOWED_TOP_KEYS)
    if unknown:
        message = (
            f"Unknown top-level key(s) in adapter '{name}' ({path}): {', '.join(sorted(unknown))}"
        )
        if _STRICT:
            raise KeyError(message)
        _warn(message + " — ignoring.")
        for key in unknown:
            data.pop(key, None)
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

    _warn(f"Unsupported clamp type {type(v).__name__} — ignoring clamp")  # pyright: ignore[reportUnknownArgumentType]
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Coercers for typed dataclasses
# ──────────────────────────────────────────────────────────────────────────────


def _coerce_buckets(v: object, name: str) -> dict[str, BucketSpec]:
    vm = _as_str_dict(v, ctx=f"Adapter '{name}': 'buckets'")
    if not vm:
        raise ValueError(f"Adapter '{name}': 'buckets' cannot be empty")

    output: dict[str, BucketSpec] = {}
    for raw_key, value in vm.items():
        key = raw_key.strip()
        if not key:
            raise ValueError(f"Adapter '{name}': bucket names cannot be empty")
        if key in output:
            raise KeyError(f"Adapter '{name}': duplicate bucket '{key}' after normalization")
        if value is None:
            output[key] = BucketSpec()
            continue
        if not isinstance(value, Mapping):
            message = f"Adapter '{name}': bucket '{key}' must be a mapping"
            if _STRICT:
                raise TypeError(message)
            _warn(message + " — using empty bucket.")
            output[key] = BucketSpec()
            continue

        bucket = _as_str_dict(
            cast(Mapping[object, object], value), ctx=f"Adapter '{name}': bucket '{key}'"
        )
        unknown = set(bucket).difference(_ALLOWED_BUCKET_KEYS)
        if unknown:
            message = (
                f"Adapter '{name}': bucket '{key}' has unknown key(s): {', '.join(sorted(unknown))}"
            )
            if _STRICT:
                raise KeyError(message)
            _warn(message + " — ignoring.")
            for unknown_key in unknown:
                bucket.pop(unknown_key, None)

        output[key] = BucketSpec(
            title=str(bucket.get("title", "")).strip(),
            description=str(bucket.get("description", "")).strip(),
            tags=_as_str_tuple(bucket.get("tags")),
            hidden=_config_bool(
                bucket.get("hidden"), ctx=f"Adapter '{name}': bucket '{key}.hidden'"
            ),
            meta=_coerce_meta_map(bucket.get("meta"), ctx=f"Adapter '{name}': bucket '{key}'.meta"),
        )
    return output


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

        dvm = _as_str_dict(
            cast(Mapping[object, object], dv), ctx=f"Adapter '{name}': dimension '{dk}'"
        )
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
        strict_val = _config_bool(
            dvm.get("strict"), ctx=f"Adapter '{name}': dimension '{dk}.strict'", default=True
        )
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

    ra = (
        _as_str_tuple(cast(object, any_headers))
        if isinstance(any_headers, (list, tuple, str))
        else ()
    )
    rl = (
        _as_str_tuple(cast(object, all_headers))
        if isinstance(all_headers, (list, tuple, str))
        else ()
    )

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

        fvm = _as_str_dict(
            cast(Mapping[object, object], fv), ctx=f"Adapter '{name}': filter '{fk}'"
        )
        unknown = set(fvm.keys()).difference(_ALLOWED_FILTER_KEYS)
        if unknown:
            msg = (
                f"Adapter '{name}': filter '{fk}' has unknown key(s): {', '.join(sorted(unknown))}"
            )
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
    """Load exactly one complete field, expression, or constant source."""
    if v is None:
        raise KeyError(f"{ctx}: source is required")
    if not isinstance(v, Mapping):
        raise TypeError(f"{ctx}: source must be a mapping (for example {{field: ppg}})")

    source = _as_str_dict(cast(Mapping[object, object], v), ctx=ctx)
    unknown = set(source).difference(_ALLOWED_SOURCE_KEYS)
    if unknown:
        message = f"{ctx}: source has unknown key(s): {', '.join(sorted(unknown))}"
        if _STRICT:
            raise KeyError(message)
        _warn(message + " — ignoring unknown source keys.")
        for key in unknown:
            source.pop(key, None)

    explicit = source.get("kind")
    if explicit is not None:
        kind = _SOURCE_KIND.get(str(explicit).strip().casefold())
        if kind is None:
            raise TypeError(f"{ctx}: source.kind must be field|expr|const")
        supplied = {key for key in ("field", "expr", "const") if key in source}
        if supplied != {kind}:
            raise TypeError(
                f"{ctx}: source kind '{kind}' requires only '{kind}', got {sorted(supplied)}"
            )
    else:
        supplied = {key for key in ("field", "expr", "const") if key in source}
        if len(supplied) != 1:
            raise TypeError(f"{ctx}: source must have exactly one of field|expr|const")
        kind = _SOURCE_KIND[next(iter(supplied))]

    if kind == "field":
        field = str(source["field"]).strip()
        if not field:
            raise ValueError(f"{ctx}: source.field cannot be empty")
        return SourceSpec(kind="field", field=field)
    if kind == "expr":
        expression = str(source["expr"]).strip()
        if not expression:
            raise ValueError(f"{ctx}: source.expr cannot be empty")
        return SourceSpec(kind="expr", expr=expression)
    return SourceSpec(
        kind="const", const=_config_float(source["const"], ctx=f"{ctx}: source.const")
    )


def _coerce_transform_param(key: str, value: object, *, ctx: str) -> Optional[MetaValue]:
    if key in _NUMERIC_TRANSFORM_KEYS:
        return _config_float(value, ctx=ctx)
    if key == "ndigits":
        number = _config_float(value, ctx=ctx)
        if not number.is_integer():
            message = f"{ctx} must be an integer, got {value!r}"
            if _STRICT:
                raise TypeError(message)
            _warn(message + " — rounding to the nearest integer.")
        return int(round(number))
    if key in {"expr", "name"}:
        result = str(value).strip()
        if not result:
            message = f"{ctx} cannot be empty"
            if _STRICT:
                raise ValueError(message)
            _warn(message + " — dropping it.")
            return None
        return result
    return _coerce_meta_value(value, ctx=ctx)


def _coerce_transform(v: object, *, ctx: str) -> Optional[TransformSpec]:
    if v is None:
        return None
    if not isinstance(v, Mapping):
        message = f"{ctx}: transform must be a mapping"
        if _STRICT:
            raise TypeError(message)
        _warn(message + " — ignoring transform.")
        return None

    transform = _as_str_dict(cast(Mapping[object, object], v), ctx=ctx)
    if "kind" not in transform:
        if "expr" in transform:
            transform["kind"] = "expr"
        elif "name" in transform:
            transform["kind"] = "custom"
        else:
            message = f"{ctx}: transform requires kind, expr, or name"
            if _STRICT:
                raise KeyError(message)
            _warn(message + " — ignoring transform.")
            return None

    kind = _TRANSFORM_KIND.get(str(transform.get("kind", "")).strip().casefold())
    if kind is None:
        message = f"{ctx}: transform.kind invalid '{transform.get('kind')}'"
        if _STRICT:
            raise TypeError(message)
        _warn(message + " — ignoring transform.")
        return None

    direct_keys = _TRANSFORM_PARAM_KEYS_BY_KIND[kind]
    unknown = set(transform).difference({"kind", "params"} | direct_keys)
    if unknown:
        message = f"{ctx}: transform has unknown key(s): {', '.join(sorted(unknown))}"
        if _STRICT:
            raise KeyError(message)
        _warn(message + " — dropping unknown keys.")

    params: dict[str, MetaValue] = {}
    nested = transform.get("params")
    if nested is not None:
        if not isinstance(nested, Mapping):
            message = f"{ctx}: transform.params must be a mapping"
            if _STRICT:
                raise TypeError(message)
            _warn(message + " — ignoring nested params.")
        else:
            nested_map = _as_str_dict(
                cast(Mapping[object, object], nested), ctx=f"{ctx}: transform.params"
            )
            nested_unknown = set(nested_map).difference(direct_keys)
            if nested_unknown:
                message = (
                    f"{ctx}: transform.params has unknown key(s) for {kind}: "
                    f"{', '.join(sorted(nested_unknown))}"
                )
                if _STRICT:
                    raise KeyError(message)
                _warn(message + " — dropping unknown params.")
            for key, value in nested_map.items():
                if key not in direct_keys:
                    continue
                coerced = _coerce_transform_param(key, value, ctx=f"{ctx}: transform.params.{key}")
                if coerced is not None:
                    params[key] = coerced

    for key in direct_keys:
        if key not in transform:
            continue
        coerced = _coerce_transform_param(key, transform[key], ctx=f"{ctx}: transform.{key}")
        if coerced is not None:
            params[key] = coerced

    if kind == "affine" and not params.keys() & {"a", "b", "scale", "offset"}:
        message = f"{ctx}: affine transform requires at least one parameter"
        if _STRICT:
            raise KeyError(message)
        _warn(message + " — ignoring transform.")
        return None

    missing = [key for key in _REQUIRED_TRANSFORM_KEYS.get(kind, ()) if key not in params]
    if missing:
        message = f"{ctx}: {kind} transform missing required parameter(s): {', '.join(missing)}"
        if _STRICT:
            raise KeyError(message)
        _warn(message + " — ignoring transform.")
        return None

    if kind == "custom":
        name = str(params["name"]).strip().casefold()
        allowed = _CUSTOM_TRANSFORM_KEYS.get(name)
        if allowed is None:
            message = f"{ctx}: unknown custom transform '{name}'"
            if _STRICT:
                raise ValueError(message)
            _warn(message + " — ignoring transform.")
            return None
        custom_unknown = set(params).difference(allowed)
        if custom_unknown:
            message = (
                f"{ctx}: custom transform '{name}' has unknown parameter(s): "
                f"{', '.join(sorted(custom_unknown))}"
            )
            if _STRICT:
                raise KeyError(message)
            _warn(message + " — dropping unknown params.")
            for key in custom_unknown:
                params.pop(key, None)
        missing_custom = [key for key in _REQUIRED_CUSTOM_KEYS.get(name, ()) if key not in params]
        if missing_custom:
            message = (
                f"{ctx}: custom transform '{name}' missing required parameter(s): "
                f"{', '.join(missing_custom)}"
            )
            if _STRICT:
                raise KeyError(message)
            _warn(message + " — ignoring transform.")
            return None
        params["name"] = name

    if kind in {"clip", "custom"} and "lo" in params and "hi" in params:
        if float(cast(float, params["lo"])) > float(cast(float, params["hi"])):
            raise ValueError(f"{ctx}: transform requires lo <= hi")
    return TransformSpec(kind=kind, params=params)


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

        pvm = _as_str_dict(
            cast(Mapping[object, object], pv), ctx=f"Adapter '{name}': score profile '{pk}'"
        )
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


def load_spec(source: str | Path) -> AdapterSpec:
    """Load one packaged adapter by name or one explicitly discovered YAML path."""
    name = source.stem if isinstance(source, Path) else str(source).strip()
    data = _read_yaml_for(source)
    _require_keys(data, name, "key", "version", "buckets", "metrics")

    key = str(data["key"]).strip()
    version = str(data["version"]).strip()
    if not key:
        raise ValueError(f"Adapter '{name}': key cannot be empty")
    if not version:
        raise ValueError(f"Adapter '{name}': version cannot be empty")
    title = str(data.get("title", key)).strip() or key
    aliases = _coerce_aliases(data.get("aliases"), key=key, ctx=f"Adapter '{name}': aliases")

    dimensions = _coerce_dimensions(data.get("dimensions"), name)
    sniff = _coerce_sniff(data.get("sniff"), name)
    filters = _coerce_filters(data.get("filters"), name)
    score_profiles = _coerce_score_profiles(data.get("score_profiles"), name)
    buckets = _coerce_buckets(data["buckets"], name)
    bucket_names = set(buckets)

    weights_raw = data.get("weights")
    weights_out: dict[str, dict[str, float]]
    if weights_raw is None:
        weights_out = _uniform_weights(sorted(bucket_names))
    else:
        weights_map = _as_str_dict(weights_raw, ctx=f"Adapter '{name}': 'weights'")
        weights_out = {}
        for raw_profile, profile_value in weights_map.items():
            profile = raw_profile.strip()
            if not profile:
                raise ValueError(f"Adapter '{name}': weight profile names cannot be empty")
            values = _as_str_dict(
                profile_value, ctx=f"Adapter '{name}': weights profile '{profile}'"
            )
            weight_inner: dict[str, float] = {bucket: 0.0 for bucket in bucket_names}
            for raw_bucket, value in values.items():
                bucket = raw_bucket.strip()
                if bucket not in bucket_names:
                    message = (
                        f"Adapter '{name}': weights profile '{profile}' references "
                        f"unknown bucket '{bucket}'"
                    )
                    if _STRICT:
                        raise KeyError(message)
                    _warn(message + " — ignoring it.")
                    continue
                weight_inner[bucket] = _finite_float(value, default=0.0)
            weights_out[profile] = weight_inner

    penalties_map = _as_str_dict(data.get("penalties"), ctx=f"Adapter '{name}': 'penalties'")
    penalties: dict[str, dict[str, float]] = {}
    for raw_profile, profile_value in penalties_map.items():
        profile = raw_profile.strip()
        if not profile:
            raise ValueError(f"Adapter '{name}': penalty profile names cannot be empty")
        values = _as_str_dict(profile_value, ctx=f"Adapter '{name}': penalties profile '{profile}'")
        penalty_inner: dict[str, float] = {}
        for raw_bucket, value in values.items():
            bucket = raw_bucket.strip()
            if bucket not in bucket_names:
                message = (
                    f"Adapter '{name}': penalties profile '{profile}' references "
                    f"unknown bucket '{bucket}'"
                )
                if _STRICT:
                    raise KeyError(message)
                _warn(message + " — dropping it.")
                continue
            penalty_inner[bucket] = _finite_float(value, default=0.0)
        penalties[profile] = penalty_inner

    metric_items = _as_obj_list(data["metrics"], ctx=f"Adapter '{name}': 'metrics'")
    metrics: list[MetricSpec] = []
    output_keys: set[str] = set()
    for index, item in enumerate(metric_items):
        if not isinstance(item, Mapping):
            raise TypeError(f"Adapter '{name}': metrics[{index}] must be a mapping")
        metric = _as_str_dict(
            cast(Mapping[object, object], item), ctx=f"Adapter '{name}': metrics[{index}]"
        )
        unknown = set(metric).difference(_ALLOWED_METRIC_KEYS)
        if unknown:
            message = (
                f"Adapter '{name}': metrics[{index}] has unknown key(s): "
                f"{', '.join(sorted(unknown))}"
            )
            if _STRICT:
                raise KeyError(message)
            _warn(message + " — ignoring unknown keys.")
            for unknown_key in unknown:
                metric.pop(unknown_key, None)
        if "key" not in metric:
            raise KeyError(f"Adapter '{name}': metrics[{index}] missing 'key'")

        metric_key = str(metric["key"]).strip()
        if not metric_key:
            raise ValueError(f"Adapter '{name}': metrics[{index}].key cannot be empty")
        if metric_key in output_keys:
            message = f"Adapter '{name}': duplicate output key '{metric_key}'"
            if _STRICT:
                raise KeyError(message)
            _warn(message + " — keeping the first.")
            continue
        output_keys.add(metric_key)

        bucket_name: Optional[str] = None
        if metric.get("bucket") is not None:
            candidate = str(metric["bucket"]).strip()
            if candidate:
                if candidate not in bucket_names:
                    message = (
                        f"Adapter '{name}': metric '{metric_key}' references "
                        f"unknown bucket '{candidate}'"
                    )
                    if _STRICT:
                        raise KeyError(message)
                    _warn(message + " — treating it as unscored telemetry.")
                else:
                    bucket_name = candidate

        metrics.append(
            MetricSpec(
                key=metric_key,
                bucket=bucket_name,
                clamp=_as_clamp(metric.get("clamp")),
                invert=_config_bool(
                    metric.get("invert"),
                    ctx=f"Adapter '{name}': metric '{metric_key}.invert'",
                ),
                source=_coerce_source(
                    metric.get("source"), ctx=f"Adapter '{name}': metric '{metric_key}'"
                ),
                transform=_coerce_transform(
                    metric.get("transform"), ctx=f"Adapter '{name}': metric '{metric_key}'"
                ),
            )
        )

    efficiency_value = data.get("efficiency")
    efficiency_items = (
        _as_obj_list(efficiency_value, ctx=f"Adapter '{name}': 'efficiency'")
        if efficiency_value is not None
        else []
    )
    efficiency: list[EffSpec] = []
    for index, item in enumerate(efficiency_items):
        if not isinstance(item, Mapping):
            raise TypeError(f"Adapter '{name}': efficiency[{index}] must be a mapping")
        entry = _as_str_dict(
            cast(Mapping[object, object], item), ctx=f"Adapter '{name}': efficiency[{index}]"
        )
        unknown = set(entry).difference(_ALLOWED_EFF_KEYS)
        if unknown:
            message = (
                f"Adapter '{name}': efficiency[{index}] has unknown key(s): "
                f"{', '.join(sorted(unknown))}"
            )
            if _STRICT:
                raise KeyError(message)
            _warn(message + " — ignoring unknown keys.")
            for unknown_key in unknown:
                entry.pop(unknown_key, None)
        for required in ("key", "make", "attempt", "bucket"):
            if required not in entry:
                raise KeyError(f"Adapter '{name}': efficiency[{index}] missing '{required}'")

        efficiency_key = str(entry["key"]).strip()
        if not efficiency_key:
            raise ValueError(f"Adapter '{name}': efficiency[{index}].key cannot be empty")
        if efficiency_key in output_keys:
            message = f"Adapter '{name}': duplicate output key '{efficiency_key}'"
            if _STRICT:
                raise KeyError(message)
            _warn(message + " — keeping the first.")
            continue
        output_keys.add(efficiency_key)

        make = str(entry["make"]).strip()
        attempt = str(entry["attempt"]).strip()
        if not make or not attempt:
            raise ValueError(
                f"Adapter '{name}': efficiency '{efficiency_key}' requires non-empty make and attempt"
            )
        bucket = str(entry["bucket"]).strip()
        if bucket not in bucket_names:
            message = f"Adapter '{name}': efficiency '{efficiency_key}' references unknown bucket '{bucket}'"
            if _STRICT:
                raise KeyError(message)
            _warn(message + " — skipping it.")
            continue

        minimum = _config_float(
            entry.get("min_den", 1.0),
            ctx=f"Adapter '{name}': efficiency '{efficiency_key}.min_den'",
        )
        if minimum <= 0:
            raise ValueError(f"Adapter '{name}': efficiency '{efficiency_key}.min_den' must be > 0")
        efficiency.append(
            EffSpec(
                key=efficiency_key,
                make=make,
                attempt=attempt,
                bucket=bucket,
                min_den=minimum,
                clamp=_as_clamp(entry.get("clamp")),
                invert=_config_bool(
                    entry.get("invert"),
                    ctx=f"Adapter '{name}': efficiency '{efficiency_key}.invert'",
                ),
                transform=_coerce_transform(
                    entry.get("transform"),
                    ctx=f"Adapter '{name}': efficiency '{efficiency_key}'",
                ),
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
        efficiency=efficiency,
        score_profiles=score_profiles,
    )
    validate_adapter(spec)
    return spec


__all__ = ["load_spec"]
