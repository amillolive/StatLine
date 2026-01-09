# statline/core/adapters/loader.py
from __future__ import annotations

import math
import os
import warnings
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, cast

import yaml

from .types import AdapterSpec, EffSpec, MetricSpec

_BASE = Path(__file__).parent / "defs"

# Configurable strictness:
#   STATLINE_LOADER_STRICT = "1" -> raise on unknown keys / unknown buckets
#   STATLINE_LOADER_STRICT = "0" (default) -> warn-and-continue with 0.0 / None
_STRICT = os.environ.get("STATLINE_LOADER_STRICT", "0") not in ("0", "", "false", "False")


def _warn(msg: str) -> None:
    warnings.warn(f"[statline.loader] {msg}", RuntimeWarning, stacklevel=2)


def _finite_float(x: Any, default: float = 0.0) -> float:
    """Coerce to finite float; warn and return default on failure/NaN/inf."""
    try:
        v = float(x)
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
    "dimensions",   # v2.1
    "sniff",        # v2.1
    "filters",      # v2.1 (metadata)
    "buckets",
    "metrics",
    "weights",
    "penalties",
    "efficiency",
    "score_profiles",
}

def _coerce_filters(v: Any, name: str) -> Dict[str, Dict[str, Any]]:
    """Coerce adapter-level declarative filter definitions (metadata)."""
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise TypeError(f"Adapter '{name}': 'filters' must be a mapping")

    out: Dict[str, Dict[str, Any]] = {}
    vm: Mapping[Any, Any] = cast(Mapping[Any, Any], v)
    for fk_any, fv_any in vm.items():
        fk = str(fk_any)
        if not isinstance(fv_any, dict):
            msg = f"Adapter '{name}': filter '{fk}' must be a mapping"
            if _STRICT:
                raise TypeError(msg)
            _warn(msg + " — ignoring.")
            continue
        out[fk] = dict(cast(Mapping[str, Any], fv_any))
    return out

def _read_yaml_for(name: str) -> Dict[str, Any]:
    p = _BASE / f"{name}.yaml"
    if not p.exists():
        p = _BASE / f"{name}.yml"
    if not p.exists():
        raise FileNotFoundError(
            f"Adapter spec not found: {name} (expected {name}.yaml or {name}.yml)"
        )

    try:
        loaded: Any = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in '{p.name}': {e}") from e

    data: Dict[str, Any]
    if loaded is None:
        data = {}
    elif isinstance(loaded, dict):
        # Force Dict[str, Any] shape; cast so keys/values aren’t Unknown to type checkers.
        loaded_map: Mapping[Any, Any] = cast(Mapping[Any, Any], loaded)
        data = {str(k): v for k, v in loaded_map.items()}
    else:
        raise TypeError(
            f"Top-level YAML for '{p.name}' must be a mapping (dict), got {type(loaded).__name__}"
        )

    # Unknown top-level keys -> warn or raise (configurable) to avoid silent typos.
    keys: set[str] = set(data.keys())
    unknown: set[str] = keys.difference(_ALLOWED_TOP_KEYS)
    if unknown:
        msg = (
            f"Unknown top-level key(s) in adapter '{name}' ({p}): "
            f"{', '.join(sorted(unknown))}"
        )
        if _STRICT:
            raise KeyError(msg)
        _warn(msg + " — ignoring.")
        for k in list(unknown):
            data.pop(k, None)

    return data


def _uniform_weights(buckets: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    keys: List[str] = list(buckets.keys())
    n = len(keys) or 1
    w = 1.0 / n
    return {"pri": {k: w for k in keys}}


def _as_clamp(v: Any) -> Optional[Tuple[float, float]]:
    """Normalize clamp configs to (lo, hi) or None. Swaps if lo > hi. Warns on bad forms."""
    if v is None or v is False:
        return None

    def _pair(lo: Any, hi: Any) -> Optional[Tuple[float, float]]:
        try:
            a = float(lo)
            b = float(hi)
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

    # Dict form: {"lo": X, "hi": Y}
    if isinstance(v, dict) and ("lo" in v and "hi" in v):
        return _pair(v["lo"], v["hi"])

    # Sequence form: [lo, hi] or (lo, hi)
    if isinstance(v, (list, tuple)):
        seq: Sequence[Any] = cast(Sequence[Any], v)
        if len(seq) >= 2:
            return _pair(seq[0], seq[1])
        _warn(f"Clamp sequence too short: {v} — ignoring clamp")
        return None

    # String forms like "0,1" / "0..1" / "0 1"
    if isinstance(v, str):
        parts: List[str] = v.replace(",", " ").replace("..", " ").split()
        if len(parts) >= 2:
            return _pair(parts[0], parts[1])
        _warn(f"Clamp string malformed: '{v}' — ignoring clamp")
        return None

    _warn(f"Unsupported clamp type {type(v).__name__} — ignoring clamp") # pyright: ignore[reportUnknownArgumentType]
    return None


def _require_keys(data: Mapping[str, Any], name: str, *req: str) -> None:
    missing = [k for k in req if k not in data]
    if missing:
        raise KeyError(f"Adapter '{name}' is missing required key(s): {', '.join(missing)}")


def _coerce_aliases(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    if isinstance(v, list):
        out: List[str] = []
        for x_any in cast(Sequence[Any], v):
            if isinstance(x_any, str):
                out.append(x_any)
        return out
    return []

def _coerce_dimensions(v: Any, name: str) -> Dict[str, Dict[str, Any]]:
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise TypeError(f"Adapter '{name}': 'dimensions' must be a mapping")

    out: Dict[str, Dict[str, Any]] = {}
    vm: Mapping[Any, Any] = cast(Mapping[Any, Any], v)
    for dk_any, dv_any in vm.items():
        dk = str(dk_any)
        if not isinstance(dv_any, dict):
            msg = f"Adapter '{name}': dimension '{dk}' must be a mapping"
            if _STRICT:
                raise TypeError(msg)
            _warn(msg + " — ignoring.")
            continue
        dvm: Mapping[Any, Any] = cast(Mapping[Any, Any], dv_any)
        vals_any = dvm.get("values")
        if not isinstance(vals_any, (list, tuple)):
            msg = f"Adapter '{name}': dimension '{dk}.values' must be a list"
            if _STRICT:
                raise TypeError(msg)
            _warn(msg + " — ignoring.")
            continue
        vals = [str(x) for x in cast(Sequence[Any], vals_any) if str(x)]
        out[dk] = {"values": vals}
    return out


def _coerce_sniff(v: Any, name: str) -> Dict[str, Any]:
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise TypeError(f"Adapter '{name}': 'sniff' must be a mapping")

    vm: Mapping[Any, Any] = cast(Mapping[Any, Any], v)
    headers_any = vm.get("require_any_headers")
    if headers_any is None:
        return {}

    if not isinstance(headers_any, (list, tuple)):
        msg = f"Adapter '{name}': sniff.require_any_headers must be a list"
        if _STRICT:
            raise TypeError(msg)
        _warn(msg + " — ignoring sniff.")
        return {}

    headers = [str(x) for x in cast(Sequence[Any], headers_any) if str(x)]
    return {"require_any_headers": headers}

def load_spec(name: str) -> AdapterSpec:
    data: Dict[str, Any] = _read_yaml_for(name)

    _require_keys(data, name, "key", "version", "buckets", "metrics")

    # Basic scalars
    key: str = str(data["key"])
    version: str = str(data["version"])
    title: str = str(data.get("title", key))
    aliases: Tuple[str, ...] = tuple(_coerce_aliases(data.get("aliases", [])))
    dimensions = _coerce_dimensions(data.get("dimensions"), name)
    sniff = _coerce_sniff(data.get("sniff"), name)
    filters = _coerce_filters(data.get("filters"), name)
    score_profiles_any: Any = data.get("score_profiles", {})
    if score_profiles_any is None:
        score_profiles = {}
    elif not isinstance(score_profiles_any, dict):
        raise TypeError(f"Adapter '{name}': 'score_profiles' must be a mapping if present")
    else:
        score_profiles = {
            str(pk): dict(cast(Mapping[str, Any], pv))
            for pk, pv in cast(Mapping[Any, Any], score_profiles_any).items()
            if isinstance(pv, dict)
        }

    # Buckets (required, must be mapping[str, dict])
    buckets_any: Any = data["buckets"]
    if not isinstance(buckets_any, dict):
        raise TypeError(f"Adapter '{name}': 'buckets' must be a mapping")
    buckets_map: Mapping[Any, Any] = cast(Mapping[Any, Any], buckets_any)
    buckets: Dict[str, Dict[str, Any]] = {
        str(bk): dict(cast(Mapping[str, Any], bv)) for bk, bv in buckets_map.items()
    }
    if not buckets:
        raise ValueError(f"Adapter '{name}': 'buckets' cannot be empty")
    bucket_names = set(buckets.keys())

    # Weights (optional; default to uniform across buckets for 'pri')
    weights_raw: Any = data.get("weights")
    weights_out: Dict[str, Dict[str, float]]
    if weights_raw is None:
        weights_out = _uniform_weights(buckets)
    else:
        if not isinstance(weights_raw, dict):
            raise TypeError(f"Adapter '{name}': 'weights' must be a mapping")
        weights_out = {}
        weights_map: Mapping[Any, Any] = cast(Mapping[Any, Any], weights_raw)
        for profile_any, bw_any in weights_map.items():
            profile = str(profile_any)
            if not isinstance(bw_any, dict):
                raise TypeError(
                    f"Adapter '{name}': weights profile '{profile}' must be a mapping"
                )
            # Initialize all known buckets to 0.0 to make omissions explicit.
            inner: Dict[str, float] = {bk: 0.0 for bk in bucket_names}
            bw_map: Mapping[Any, Any] = cast(Mapping[Any, Any], bw_any)
            for b_any, v_any in bw_map.items():
                bk = str(b_any)
                if bk not in bucket_names:
                    msg = (
                        f"Adapter '{name}': weights profile '{profile}' references "
                        f"unknown bucket '{bk}'"
                    )
                    if _STRICT:
                        raise KeyError(msg)
                    _warn(msg + " — treating as 0.0 and ignoring.")
                    continue
                inner[bk] = _finite_float(v_any, default=0.0)
            weights_out[profile] = inner

    # Penalties (optional; adapter-defined semantics). Keep as {profile: {key: float}}.
    penalties_raw: Any = data.get("penalties", {})
    if not isinstance(penalties_raw, dict):
        raise TypeError(f"Adapter '{name}': 'penalties' must be a mapping if present")
    penalties: Dict[str, Dict[str, float]] = {}
    penalties_map: Mapping[Any, Any] = cast(Mapping[Any, Any], penalties_raw)
    for profile_any, pw_any in penalties_map.items():
        profile = str(profile_any)
        if not isinstance(pw_any, dict):
            raise TypeError(
                f"Adapter '{name}': penalties profile '{profile}' must be a mapping"
            )
        inner_p: Dict[str, float] = {}
        pw_map: Mapping[Any, Any] = cast(Mapping[Any, Any], pw_any)
        for k_any, v_any in pw_map.items():
            bk = str(k_any)
            if bk not in bucket_names:
                msg = (
                    f"Adapter '{name}': penalties profile '{profile}' references "
                    f"unknown bucket '{bk}'"
                )
                if _STRICT:
                    raise KeyError(msg)
                _warn(msg + " — dropping penalty.")
                continue
            inner_p[bk] = _finite_float(v_any, default=0.0)
        penalties[profile] = inner_p

    # Metrics
    metrics_val: Any = data["metrics"]
    if not isinstance(metrics_val, list):
        raise TypeError(f"Adapter '{name}': 'metrics' must be a list")
    metrics: List[MetricSpec] = []
    seen_keys: set[str] = set()
    for m_any in cast(Sequence[Any], metrics_val):
        if not isinstance(m_any, dict):
            raise TypeError(f"Adapter '{name}': each metric must be a mapping")
        m: Mapping[str, Any] = cast(Mapping[str, Any], m_any)
        if "key" not in m:
            raise KeyError(f"Adapter '{name}': every metric must have a 'key'")
        mkey = str(m["key"])
        if mkey in seen_keys:
            _warn(
                f"Adapter '{name}': duplicate metric key '{mkey}' — keeping first, skipping duplicate."
            )
            continue
        seen_keys.add(mkey)

        bucket_val: Any = m.get("bucket")
        bucket_name: Optional[str] = None
        if bucket_val is not None:
            bname = str(bucket_val)
            if bname not in bucket_names:
                msg = (
                    f"Adapter '{name}': metric '{mkey}' references unknown bucket '{bname}'"
                )
                if _STRICT:
                    raise KeyError(msg)
                _warn(msg + " — treating as unscored telemetry (no bucket).")
            else:
                bucket_name = bname

        metrics.append(
            MetricSpec(
                key=mkey,
                bucket=bucket_name,
                clamp=_as_clamp(m.get("clamp")),
                invert=bool(m.get("invert", False)),
                source=cast(Optional[Mapping[str, Any]], m.get("source")),
                transform=cast(Optional[Mapping[str, Any]], m.get("transform")),
            )
        )

    # Efficiency (optional)
    eff_list: List[EffSpec] = []
    eff_any: Any = data.get("efficiency", [])
    if not isinstance(eff_any, list):
        raise TypeError(f"Adapter '{name}': 'efficiency' must be a list if present")
    for e_any in cast(Sequence[Any], eff_any):
        if not isinstance(e_any, dict):
            raise TypeError(f"Adapter '{name}': efficiency items must be mappings")
        e: Mapping[str, Any] = cast(Mapping[str, Any], e_any)
        for req in ("key", "make", "attempt", "bucket"):
            if req not in e:
                raise KeyError(f"Adapter '{name}': efficiency item missing '{req}'")
        ekey = str(e["key"])
        ebucket = str(e["bucket"])
        if ebucket not in bucket_names:
            msg = (
                f"Adapter '{name}': efficiency '{ekey}' references unknown bucket '{ebucket}'"
            )
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
                transform=cast(Optional[Mapping[str, Any]], e.get("transform")),
            )
        )

    # Final spec (strict, adapter-only)
    return AdapterSpec(
        key=key,
        version=version,
        aliases=aliases,
        title=title,
        dimensions=dimensions,
        sniff=sniff,
        filters=filters,
        score_profiles=score_profiles,
        buckets=buckets,
        metrics=metrics,
        weights=weights_out,
        penalties=penalties,
        efficiency=eff_list,
    )

__all__ = ["load_spec"]