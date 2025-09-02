from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, cast

import yaml

from .types import AdapterSpec, EffSpec, MetricSpec

_BASE = Path(__file__).parent / "defs"

def _read_yaml_for(name: str) -> Dict[str, Any]:
    p = _BASE / f"{name}.yaml"
    if not p.exists():
        p = _BASE / f"{name}.yml"
    if not p.exists():
        raise FileNotFoundError(f"Adapter spec not found: {name}")
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    return cast(Dict[str, Any], data or {})

def _uniform_weights(buckets: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    keys = list(buckets.keys())
    n = len(keys) or 1
    w = 1.0 / n
    return {"pri": {k: w for k in keys}}

def _as_clamp(v: Any) -> Optional[Tuple[float, float]]:
    """Normalize clamp configs to (lo, hi) or None."""
    if v is None or v is False:
        return None

    # Sequence form: [lo, hi] or (lo, hi)
    if isinstance(v, (list, tuple)):
        seq: Sequence[Any] = cast(Sequence[Any], v)
        if len(seq) >= 2:
            a: Any = seq[0]
            b: Any = seq[1]
            return (float(a), float(b))
        return None

    # String-ish forms like "0,1" or "0..1"
    if isinstance(v, str):
        parts: List[str] = v.replace(",", " ").replace("..", " ").split()
        if len(parts) >= 2:
            return (float(parts[0]), float(parts[1]))
        return None

    return None

def load_spec(name: str) -> AdapterSpec:
    data: Dict[str, Any] = _read_yaml_for(name)

    for req in ("key", "version", "buckets", "metrics"):
        if req not in data:
            raise KeyError(f"Adapter '{name}' is missing required key: {req}")

    buckets: Dict[str, Dict[str, Any]] = cast(Dict[str, Dict[str, Any]], data["buckets"])
    weights: Dict[str, Dict[str, float]] = cast(
        Dict[str, Dict[str, float]],
        data.get("weights") or _uniform_weights(buckets),
    )
    penalties: Dict[str, Dict[str, float]] = cast(
        Dict[str, Dict[str, float]], data.get("penalties", {})
    )

    metrics: List[MetricSpec] = []
    for m in cast(List[Mapping[str, Any]], data["metrics"]):
        metrics.append(
            MetricSpec(
                key=str(m["key"]),
                bucket=str(m.get("bucket")) if "bucket" in m else None,
                clamp=_as_clamp(m.get("clamp")),
                invert=bool(m.get("invert", False)),
                source=cast(Optional[Mapping[str, Any]], m.get("source")),
                transform=cast(Optional[Mapping[str, Any]], m.get("transform")),
            )
        )

    eff_list: List[EffSpec] = []
    for e in cast(List[Mapping[str, Any]], data.get("efficiency", [])):
        eff_list.append(
            EffSpec(
                key=str(e["key"]),
                make=str(e["make"]),
                attempt=str(e["attempt"]),
                bucket=str(e["bucket"]),
                min_den=float(e.get("min_den", 1.0)),
                clamp=_as_clamp(e.get("clamp")),
                invert=bool(e.get("invert", False)),
                transform=cast(Optional[Mapping[str, Any]], e.get("transform")),
            )
        )

    # Legacy mapping removed/ignored (strict-only)

    return AdapterSpec(
        key=str(data["key"]),
        version=str(data["version"]),
        aliases=tuple(cast(List[str], data.get("aliases", []))),
        title=str(data.get("title", data["key"])),
        buckets=buckets,
        metrics=metrics,
        weights=weights,
        penalties=penalties,
        efficiency=eff_list,
    )
