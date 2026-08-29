# statline/core/weights.py
from __future__ import annotations

from typing import Dict, Iterable, Mapping, Optional, SupportsFloat


def normalize_weights(weights: Mapping[str, SupportsFloat]) -> dict[str, float]:
    """
    L1-normalize a mapping of weights (ints/floats) so the sum of absolute values is 1.0.

    - Preserves sign (negative weights are allowed to represent penalties).
    - If all weights are zero or the mapping is empty, returns {}.
    """
    total = float(sum(abs(float(v)) for v in weights.values()))
    if total <= 0.0:
        return {}
    return {str(k): float(v) / total for k, v in weights.items()}


def resolve_weights(
    metrics: Iterable[str],
    *,
    defaults: Optional[Mapping[str, SupportsFloat]] = None,
    override: Optional[Mapping[str, SupportsFloat]] = None,
    fill_missing_with_zero: bool = True,
) -> dict[str, float]:
    """
    Merge default weights with user/project overrides (override wins), then normalize.

    Parameters
    ----------
    metrics
        Canonical metric keys for the current adapter (whatever it emits).
    defaults
        Adapter-provided baseline weights (optional).
    override
        Sparse mapping of weights supplied by the caller to change specific entries (optional).
    fill_missing_with_zero
        If True (default), any metric not present after merging is assigned 0 weight.

    Returns
    -------
    dict[str, float]
        L1-normalized weights over the provided metrics. May be {} if everything is zero.
    """
    merged: Dict[str, float] = {}

    # Start from defaults
    if defaults:
        for k, v in defaults.items():
            merged[str(k)] = float(v)

    # Apply overrides
    if override:
        for k, v in override.items():
            merged[str(k)] = float(v)

    # Ensure only known metrics remain; optionally fill missing with zeros
    metric_set = set(metrics)
    merged = {k: v for k, v in merged.items() if k in metric_set}
    if fill_missing_with_zero:
        for m in metric_set:
            merged.setdefault(m, 0.0)

    return normalize_weights(merged)


def pick_profile(
    profiles: Mapping[str, Mapping[str, SupportsFloat]] | None,
    name: str | None,
) -> Mapping[str, SupportsFloat]:
    """
    Select a weight profile by name from an adapter's provided profiles.

    Behavior:
    - If `profiles` is falsy, return {}.
    - If `name` exists in `profiles`, return that mapping.
    - Else, if "default" exists, return it.
    - Else, return the first mapping deterministically.
    """
    if not profiles:
        return {}
    if name and name in profiles:
        return profiles[name]
    if "default" in profiles:
        return profiles["default"]
    first_key = next(iter(profiles.keys()))
    return profiles[first_key]
