# statline/core/adapters/loader.py
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, cast

import yaml

from .types import AdapterSpec, EffSpec, MetricSpec

_BASE = Path(__file__).parent / "defs"

# Allowed top-level keys in an adapter YAML (helps catch typos).
_ALLOWED_TOP_KEYS: set[str] = {
    "key",
    "version",
    "aliases",
    "title",
    "buckets",
    "metrics",
    "weights",
    "penalties",
    "efficiency",
}


def _read_yaml_for(name: str) -> Dict[str, Any]:
    p = _BASE / f"{name}.yaml"
    if not p.exists():
        p = _BASE / f"{name}.yml"
    if not p.exists():
        raise FileNotFoundError(f"Adapter spec not found: {name} (expected {name}.yaml or {name}.yml)")

    try:
        loaded: Any = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in '{p.name}': {e}") from e

    data: Dict[str, Any]
    if loaded is None:
        data = {}
    elif isinstance(loaded, dict):
        # Force Dict[str, Any] shape; cast so keys/values aren’t Unknown to Pylance.
        loaded_map: Mapping[Any, Any] = cast(Mapping[Any, Any], loaded)
        data = {str(k): v for k, v in loaded_map.items()}
    else:
        raise TypeError(f"Top-level YAML for '{p.name}' must be a mapping (dict), got {type(loaded).__name__}")

    # Unknown top-level keys -> explicit error to avoid silent typos.
    keys: set[str] = set(data.keys())
    unknown: set[str] = keys.difference(_ALLOWED_TOP_KEYS)
    if unknown:
        raise KeyError(f"Unknown top-level key(s) in adapter '{name}': {', '.join(sorted(unknown))}")
    return data


def _uniform_weights(buckets: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    keys: List[str] = list(buckets.keys())
    n = len(keys) or 1
    w = 1.0 / n
    return {"pri": {k: w for k in keys}}


def _as_clamp(v: Any) -> Optional[Tuple[float, float]]:
    """Normalize clamp configs to (lo, hi) or None. Swaps if lo > hi."""
    if v is None or v is False:
        return None

    # Sequence form: [lo, hi] or (lo, hi)
    if isinstance(v, (list, tuple)):
        seq: Sequence[Any] = cast(Sequence[Any], v)
        if len(seq) >= 2:
            try:
                lo = float(seq[0])
                hi = float(seq[1])
            except (TypeError, ValueError):
                return None
            if lo > hi:
                lo, hi = hi, lo
            return (lo, hi)
        return None

    # String forms like "0,1" / "0..1" / "0 1"
    if isinstance(v, str):
        parts: List[str] = v.replace(",", " ").replace("..", " ").split()
        if len(parts) >= 2:
            try:
                lo = float(parts[0])
                hi = float(parts[1])
            except ValueError:
                return None
            if lo > hi:
                lo, hi = hi, lo
            return (lo, hi)
        return None

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


def load_spec(name: str) -> AdapterSpec:
    data: Dict[str, Any] = _read_yaml_for(name)

    _require_keys(data, name, "key", "version", "buckets", "metrics")

    # Basic scalars
    key: str = str(data["key"])
    version: str = str(data["version"])
    title: str = str(data.get("title", key))
    aliases: Tuple[str, ...] = tuple(_coerce_aliases(data.get("aliases", [])))

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
                raise TypeError(f"Adapter '{name}': weights profile '{profile}' must be a mapping")
            inner: Dict[str, float] = {}
            bw_map: Mapping[Any, Any] = cast(Mapping[Any, Any], bw_any)
            for b_any, v_any in bw_map.items():
                inner[str(b_any)] = float(v_any)
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
            raise TypeError(f"Adapter '{name}': penalties profile '{profile}' must be a mapping")
        inner_p: Dict[str, float] = {}
        pw_map: Mapping[Any, Any] = cast(Mapping[Any, Any], pw_any)
        for k_any, v_any in pw_map.items():
            inner_p[str(k_any)] = float(v_any)
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
            raise ValueError(f"Adapter '{name}': duplicate metric key '{mkey}'")
        seen_keys.add(mkey)

        bucket_val: Any = m.get("bucket")
        bucket_name: Optional[str] = None
        if bucket_val is not None:
            bname = str(bucket_val)
            if bname not in buckets:
                raise KeyError(f"Adapter '{name}': metric '{mkey}' references unknown bucket '{bname}'")
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
        if ebucket not in buckets:
            raise KeyError(f"Adapter '{name}': efficiency '{ekey}' references unknown bucket '{ebucket}'")
        eff_list.append(
            EffSpec(
                key=ekey,
                make=str(e["make"]),
                attempt=str(e["attempt"]),
                bucket=ebucket,
                min_den=float(e.get("min_den", 1.0)),
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
        buckets=buckets,
        metrics=metrics,
        weights=weights_out,
        penalties=penalties,
        efficiency=eff_list,
    )


__all__ = ["load_spec"]
